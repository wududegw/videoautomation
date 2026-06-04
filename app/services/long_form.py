"""
Long-form pipeline helpers for MoneyPrinterTurbo (Long edition).

This module is a thin layer on top of the existing services. It does NOT
replace the short-form pipeline — it sits next to it. The original
`tts()`, `combine_videos()`, and `generate_script()` keep working
unchanged. When `params.long_form == True`, `task.start()` instead
delegates to functions in this file.

Why long-form needs its own path:
1. LLMs cap output at ~4–8k tokens, so a 1-hour script must be generated
   chapter by chapter. We outline the topic first, then prompt the LLM
   per chapter, then stitch the chapters back into one script.
2. Edge-TTS streams degrade and disconnect for very long inputs; we chunk
   the script by sentence/paragraph, synthesize each chunk to its own
   MP3, then concatenate with the ffmpeg concat demuxer.
3. MoviePy re-encodes every subclip via `write_videofile`, which dominates
   wall-clock time for hour-long renders. We instead use ffmpeg directly
   with the `concat` demuxer plus stream copy where possible, and only
   touch MoviePy for the final subtitle/audio overlay step.

This file intentionally has no MoviePy frame-decoding loops.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from loguru import logger

from app.config import config
from app.services import llm, voice
from app.services.video import (
    _escape_ffmpeg_concat_path,
    delete_files,
    get_ffmpeg_binary,
)
from app.utils import utils


# ---------------------------------------------------------------------------
# Encoder auto-detection (NVENC > QSV > AMF > libx264)
# ---------------------------------------------------------------------------


_ENCODER_CACHE: Optional[dict] = None


def _probe_encoder(name: str) -> bool:
    """Return True if ffmpeg can actually open this encoder on this machine."""
    ffmpeg = get_ffmpeg_binary()
    try:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.1:r=30",
            "-frames:v",
            "1",
            "-c:v",
            name,
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False


def _detect_video_encoder() -> dict:
    """
    Pick the fastest available H.264 encoder. Quality targets are tuned to
    match libx264 -crf 23 visually so the output looks the same as before.
    """
    global _ENCODER_CACHE
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE

    candidates = [
        (
            "h264_nvenc",
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                "-tune",
                "hq",
                "-rc",
                "vbr",
                "-cq",
                "23",
                "-b:v",
                "0",
            ],
        ),
        (
            "h264_qsv",
            [
                "-c:v",
                "h264_qsv",
                "-preset",
                "faster",
                "-global_quality",
                "23",
            ],
        ),
        (
            "h264_amf",
            [
                "-c:v",
                "h264_amf",
                "-quality",
                "speed",
                "-rc",
                "cqp",
                "-qp_i",
                "23",
                "-qp_p",
                "23",
            ],
        ),
    ]

    for name, args in candidates:
        if _probe_encoder(name):
            logger.info(f"long-form: using hardware encoder {name}")
            _ENCODER_CACHE = {"name": name, "args": args}
            return _ENCODER_CACHE

    logger.info("long-form: using libx264 (no hardware encoder available)")
    _ENCODER_CACHE = {
        "name": "libx264",
        "args": [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
        ],
    }
    return _ENCODER_CACHE


# ---------------------------------------------------------------------------
# Chunked LLM script generation
# ---------------------------------------------------------------------------


# Words per minute used to estimate script length from target_minutes.
# 150 wpm is a comfortable narration pace for documentary/explainer style
# content; bump this if your TTS voice speaks faster.
_NARRATION_WPM = 150

# How many chapters to break a long script into. 1 chapter ≈ 5 minutes of
# narration at the default WPM. We cap at 24 to keep LLM calls bounded.
_MAX_CHAPTERS = 24


@dataclass
class ChapterPlan:
    index: int
    title: str
    target_words: int


def _estimate_words_from_minutes(target_minutes: float) -> int:
    return max(int(round(target_minutes * _NARRATION_WPM)), 200)


def _plan_chapters(
    subject: str, target_minutes: float, language: str = ""
) -> List[ChapterPlan]:
    """
    Ask the LLM to produce a chapter-by-chapter outline for the topic.

    We deliberately keep this prompt tiny so it stays well under any
    model's output limit. If the LLM returns malformed JSON we fall back
    to a synthetic outline that just splits the topic into N equal parts.
    """
    total_minutes = max(float(target_minutes), 1.0)
    n_chapters = min(_MAX_CHAPTERS, max(int(math.ceil(total_minutes / 5.0)), 2))
    total_words = _estimate_words_from_minutes(total_minutes)
    words_per_chapter = max(total_words // n_chapters, 200)

    outline_prompt = (
        f"You are planning a {total_minutes:.0f}-minute spoken-narration video "
        f"about: {subject}.\n"
        f"Return a JSON array of exactly {n_chapters} short chapter titles. "
        f"No numbering, no markdown, no extra prose — JSON array only.\n"
        f'Example: ["Origin and discovery", "How it actually works", ...]'
    )
    if language:
        outline_prompt += f"\nUse this language for the titles: {language}"

    raw = ""
    try:
        raw = llm._generate_response(outline_prompt) or ""
    except Exception as exc:
        logger.warning(f"chapter outline LLM call failed: {exc}")

    titles: List[str] = []
    if raw:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                import json

                titles = json.loads(match.group(0))
                if not isinstance(titles, list):
                    titles = []
            except Exception:
                titles = []

    # Fallback outline if the LLM misbehaves: synthetic chapter titles.
    if not titles:
        logger.warning(
            "outline LLM response was unparseable, falling back to synthetic chapters"
        )
        titles = [f"Part {i + 1} of {subject}" for i in range(n_chapters)]

    # Trim/pad to exactly n_chapters.
    titles = titles[:n_chapters]
    while len(titles) < n_chapters:
        titles.append(f"Part {len(titles) + 1} of {subject}")

    return [
        ChapterPlan(index=i + 1, title=t.strip() or f"Part {i + 1}",
                    target_words=words_per_chapter)
        for i, t in enumerate(titles)
    ]


def _write_chapter(
    subject: str,
    plan: ChapterPlan,
    prior_titles: List[str],
    language: str = "",
) -> str:
    """
    Generate ~target_words of narration for a single chapter.

    We provide the prior chapter titles as context so the model doesn't
    repeat itself, but we never feed the full prior chapter bodies — those
    would explode the prompt. Continuity across chapters is good-enough
    for narrated explainer videos.
    """
    prior = "\n".join(f"- {t}" for t in prior_titles) if prior_titles else "(none)"
    prompt = (
        f"You are writing a single chapter of a long-form spoken-narration video.\n"
        f"Topic: {subject}\n"
        f"Chapter title: {plan.title}\n"
        f"Previous chapter titles (for continuity, do not repeat them):\n{prior}\n\n"
        f"Constraints:\n"
        f"1. Write ONLY the spoken narration for this chapter, in clear sentences.\n"
        f"2. Target length: about {plan.target_words} words.\n"
        f"3. No headings, no bullet points, no markdown, no 'voiceover:' labels.\n"
        f"4. Don't say 'In this chapter' or 'Welcome'. Get straight into the content.\n"
        f"5. End with a natural sentence — don't tease the next chapter."
    )
    if language:
        prompt += f"\n6. Language: {language}"

    text = ""
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            text = llm._generate_response(prompt) or ""
        except Exception as exc:
            last_err = exc
            logger.warning(
                f"chapter {plan.index} attempt {attempt + 1} failed: {exc}"
            )
            continue
        text = text.replace("*", "").replace("#", "")
        text = re.sub(r"\[.*?\]", "", text)
        text = text.strip()
        if text:
            break

    if not text:
        # Last-resort placeholder so the pipeline doesn't die mid-render.
        logger.error(
            f"chapter {plan.index} '{plan.title}' produced no text"
            + (f" (last error: {last_err})" if last_err else "")
        )
        text = (
            f"This chapter of {subject} could not be generated automatically. "
            "Skipping to the next section."
        )

    return text


def generate_long_script(
    subject: str,
    target_minutes: float,
    language: str = "",
    progress: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Produce a single long script by stitching together chapter-sized LLM calls.

    Returns the full narration as one string (paragraphs separated by blank
    lines so chunked TTS can use those as natural cut points).
    """
    plans = _plan_chapters(subject, target_minutes, language=language)
    logger.info(
        f"long-form script plan: {len(plans)} chapters, "
        f"~{_estimate_words_from_minutes(target_minutes)} words total"
    )

    chapter_texts: List[str] = []
    prior_titles: List[str] = []
    for plan in plans:
        if progress:
            progress(f"writing chapter {plan.index}/{len(plans)}: {plan.title}")
        logger.info(
            f"writing chapter {plan.index}/{len(plans)}: {plan.title} "
            f"(~{plan.target_words} words)"
        )
        chapter_texts.append(_write_chapter(subject, plan, prior_titles, language))
        prior_titles.append(plan.title)

    return "\n\n".join(chapter_texts).strip()


# ---------------------------------------------------------------------------
# Chunked TTS + ffmpeg audio concat
# ---------------------------------------------------------------------------


# Hard upper bound on a single TTS chunk so Edge-TTS doesn't time out.
# Roughly 4–5 minutes of narration at 150 wpm.
_MAX_CHUNK_CHARS = 4000


def _split_for_tts(text: str, max_chars: Optional[int] = None) -> List[str]:
    """
    Split text into TTS-friendly chunks, preferring paragraph breaks first
    and sentence boundaries second. Never splits inside a sentence unless
    the sentence is itself longer than max_chars. Resolves max_chars at call
    time so tests / runtime tweaks to `_MAX_CHUNK_CHARS` take effect.
    """
    if max_chars is None:
        max_chars = _MAX_CHUNK_CHARS
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    current = ""

    def _flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            # Paragraph itself is huge — split on sentence boundaries.
            _flush()
            sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph)
            sub_current = ""
            for sentence in sentences:
                if not sentence.strip():
                    continue
                if len(sub_current) + len(sentence) + 1 > max_chars and sub_current:
                    chunks.append(sub_current.strip())
                    sub_current = sentence
                else:
                    sub_current = (sub_current + " " + sentence).strip()
            if sub_current.strip():
                chunks.append(sub_current.strip())
            continue

        if len(current) + len(paragraph) + 2 > max_chars and current:
            _flush()
        current = (current + "\n\n" + paragraph).strip()

    _flush()
    return chunks


def _concat_audio_with_ffmpeg(input_files: List[str], output_file: str) -> None:
    """Concatenate MP3s with the ffmpeg concat demuxer (no re-encode)."""
    if not input_files:
        raise ValueError("no audio files to concatenate")

    out_dir = os.path.dirname(os.path.abspath(output_file)) or "."
    os.makedirs(out_dir, exist_ok=True)
    list_path = os.path.join(out_dir, "ffmpeg-audio-concat.txt")
    with open(list_path, "w", encoding="utf-8") as fp:
        for path in input_files:
            fp.write(
                f"file '{_escape_ffmpeg_concat_path(os.path.abspath(path))}'\n"
            )

    cmd = [
        get_ffmpeg_binary(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        # MP3 doesn't always concat cleanly without re-encoding when bitrates
        # differ; re-encode at a sane CBR so the final timeline is reliable.
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        output_file,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "ffmpeg audio concat failed").strip()
            )
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def tts_long(
    text: str,
    voice_name: str,
    voice_rate: float,
    voice_file: str,
    voice_volume: float = 1.0,
    progress: Optional[Callable[[str], None]] = None,
) -> Optional[object]:
    """
    Long-form replacement for `voice.tts()`.

    Splits the script, calls `voice.tts()` per chunk into a temporary file,
    then concatenates the chunks into the final `voice_file`. Returns the
    first chunk's SubMaker so the existing subtitle pipeline still has a
    handle to feed if it needs one. For long scripts, generate subtitles from
    the stitched script and merged audio duration instead of reusing chunk 1's
    Edge-TTS word boundaries.
    """
    chunks = _split_for_tts(text)
    if not chunks:
        logger.error("tts_long: empty input text")
        return None

    if len(chunks) == 1:
        # No need to spin up a temp dir for a single chunk.
        return voice.tts(
            text=chunks[0],
            voice_name=voice_name,
            voice_rate=voice_rate,
            voice_file=voice_file,
            voice_volume=voice_volume,
        )

    out_dir = os.path.dirname(os.path.abspath(voice_file)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="tts-long-", dir=out_dir)
    chunk_files: List[str] = []
    first_sub_maker = None

    try:
        for i, chunk in enumerate(chunks):
            chunk_path = os.path.join(tmp_dir, f"chunk-{i + 1:04d}.mp3")
            if progress:
                progress(f"tts chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)")
            logger.info(f"tts chunk {i + 1}/{len(chunks)}, chars={len(chunk)}")
            t0 = time.time()
            sub_maker = voice.tts(
                text=chunk,
                voice_name=voice_name,
                voice_rate=voice_rate,
                voice_file=chunk_path,
                voice_volume=voice_volume,
            )
            elapsed = time.time() - t0
            if sub_maker is None or not os.path.exists(chunk_path):
                logger.error(f"tts chunk {i + 1} failed (elapsed {elapsed:.1f}s)")
                return None
            if first_sub_maker is None:
                first_sub_maker = sub_maker
            chunk_files.append(chunk_path)

        _concat_audio_with_ffmpeg(chunk_files, voice_file)
        return first_sub_maker
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fast script-timed subtitles
# ---------------------------------------------------------------------------


def _split_for_subtitles(text: str, max_chars: int = 72) -> List[str]:
    """
    Split narration into compact subtitle cues.

    This is intentionally deterministic and fast. Whisper can be more precise,
    but CPU transcription of long/repetitive scripts can hang for minutes.
    """
    text = re.sub(r"[#*_`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    pieces = re.split(r"(?<=[.!?。！？])\s+", text)
    cues: List[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            cues.append(current.strip())
        current = ""

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue

        words = piece.split()
        if len(piece) > max_chars and len(words) > 1:
            flush()
            chunk = ""
            for word in words:
                candidate = (chunk + " " + word).strip()
                if len(candidate) > max_chars and chunk:
                    cues.append(chunk.strip())
                    chunk = word
                else:
                    chunk = candidate
            if chunk.strip():
                cues.append(chunk.strip())
            continue

        candidate = (current + " " + piece).strip()
        if len(candidate) > max_chars and current:
            flush()
            current = piece
        else:
            current = candidate

    flush()
    return cues


def create_script_timed_subtitle(
    text: str,
    audio_file: str,
    subtitle_file: str,
) -> str:
    """
    Create SRT subtitles by distributing script cues across the audio duration.

    This is less frame-perfect than Whisper, but it is reliable for hour-long
    videos and avoids the CPU Whisper deadlock seen with repetitive fact lists.
    """
    cues = _split_for_subtitles(text)
    if not cues:
        logger.warning("script-timed subtitle generation skipped: empty script")
        return ""

    duration = _ffprobe_duration(audio_file)
    if duration <= 0:
        duration = float(voice.get_audio_duration(audio_file) or 0)
    if duration <= 0:
        logger.warning("script-timed subtitle generation skipped: unknown audio duration")
        return ""

    total_weight = sum(max(len(cue), 1) for cue in cues)
    elapsed_weight = 0
    lines: List[str] = []

    for index, cue in enumerate(cues, start=1):
        weight = max(len(cue), 1)
        start = duration * elapsed_weight / total_weight
        elapsed_weight += weight
        end = duration * elapsed_weight / total_weight

        if index == len(cues):
            end = duration
        if end <= start:
            end = min(start + 1.0, duration)

        lines.append(utils.text_to_srt(index, cue, start, end))

    os.makedirs(os.path.dirname(os.path.abspath(subtitle_file)), exist_ok=True)
    with open(subtitle_file, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")

    logger.info(
        f"script-timed subtitle file created: {subtitle_file}, "
        f"cues={len(cues)}, duration={duration:.2f}s"
    )
    return subtitle_file


# ---------------------------------------------------------------------------
# ffmpeg-only video assembly (no per-clip MoviePy re-encode)
# ---------------------------------------------------------------------------


def _ffprobe_duration(path: str) -> float:
    """Read a media file's duration in seconds via ffprobe; 0 if it fails."""
    ffmpeg = get_ffmpeg_binary()
    # ffprobe usually sits next to ffmpeg in the same bin directory.
    ffprobe = ffmpeg.replace("ffmpeg.exe", "ffprobe.exe").replace(
        "ffmpeg", "ffprobe"
    )
    if not shutil.which(ffprobe) and not os.path.isfile(ffprobe):
        # Fall back to using ffmpeg itself to read duration from stderr.
        ffprobe = None

    if ffprobe:
        try:
            out = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            value = (out.stdout or "").strip()
            if value:
                return float(value)
        except Exception as exc:
            logger.debug(f"ffprobe failed for {path}: {exc}")
    return 0.0


def _normalize_clip_with_ffmpeg(
    src: str,
    dst: str,
    width: int,
    height: int,
    threads: int,
    clip_duration: float,
) -> bool:
    """
    Re-encode a single source clip to a fixed resolution/fps/codec so it can
    later be stream-copied during concat. Trims to `clip_duration` seconds,
    pads + scales to fit the target aspect ratio.
    """
    vf = (
        f"scale='if(gt(iw/ih,{width}/{height}),{width},-2)':"
        f"'if(gt(iw/ih,{width}/{height}),-2,{height})',"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=30"
    )
    cmd = [
        get_ffmpeg_binary(),
        "-y",
        "-t",
        f"{clip_duration:.3f}",
        "-i",
        src,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-an",  # drop source audio; narration is added later
        "-threads",
        str(max(int(threads), 1)),
        dst,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.warning(
            f"ffmpeg normalize failed for {src}: "
            f"{(result.stderr or '').strip().splitlines()[-1:]}"
        )
        return False
    return True


def _encode_slice(
    src: str,
    dst: str,
    start: float,
    duration: float,
    width: int,
    height: int,
    threads_per_job: int,
    encoder: dict,
) -> bool:
    """Encode a single slice of a source clip to the normalized target format."""
    vf = (
        f"scale='if(gt(iw/ih,{width}/{height}),{width},-2)':"
        f"'if(gt(iw/ih,{width}/{height}),-2,{height})',"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=30"
    )
    cmd = [
        get_ffmpeg_binary(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        src,
        "-vf",
        vf,
        *encoder["args"],
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-threads",
        str(max(int(threads_per_job), 1)),
        dst,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and os.path.exists(dst):
        return True
    logger.warning(
        f"skip slice {src} [{start:.1f}s..{start + duration:.1f}s]: "
        f"{(result.stderr or '').strip().splitlines()[-1:]}"
    )
    return False


def combine_videos_fast(
    combined_video_path: str,
    video_paths: List[str],
    audio_duration: float,
    width: int,
    height: int,
    max_clip_duration: int,
    threads: int = 4,
    shuffle: bool = True,
) -> str:
    """
    ffmpeg-only video assembly for long-form. Tuned for speed:

    1. Plan the slice playlist FIRST — only schedule enough slices to cover
       ``audio_duration``. We never normalize clips we won't use.
    2. Encode the planned slices in parallel with the fastest available
       hardware encoder (NVENC > QSV > AMF > libx264).
    3. Concat the normalized slices via the ffmpeg concat demuxer using
       stream copy (no second encode pass).
    """
    import random

    if not video_paths:
        raise ValueError("combine_videos_fast: no source clips provided")

    encoder = _detect_video_encoder()
    out_dir = os.path.dirname(os.path.abspath(combined_video_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix="combine-fast-", dir=out_dir)

    try:
        # Step 1: probe durations once, build the list of candidate slices
        # without encoding anything yet.
        candidate_slices: List[Tuple[str, float, float]] = []  # (src, start, dur)
        for src in video_paths:
            try:
                duration = _ffprobe_duration(src)
            except Exception:
                duration = 0.0
            if duration <= 0:
                duration = float(max_clip_duration)

            start = 0.0
            while start < duration:
                end = min(start + max_clip_duration, duration)
                candidate_slices.append((src, start, end - start))
                start = end

        if not candidate_slices:
            raise RuntimeError("no candidate slices found; cannot assemble video")

        if shuffle:
            random.shuffle(candidate_slices)

        # Step 2: choose just enough slices to cover audio_duration (with a
        # small safety margin so the final concat is never shorter than the
        # narration). We loop the candidate list if there aren't enough.
        target_seconds = audio_duration + max_clip_duration
        planned: List[Tuple[str, float, float]] = []
        running = 0.0
        idx = 0
        while running < target_seconds and idx < len(candidate_slices) * 20:
            sl = candidate_slices[idx % len(candidate_slices)]
            planned.append(sl)
            running += sl[2]
            idx += 1

        logger.info(
            f"long-form combine: planning {len(planned)} slices "
            f"(~{running:.0f}s) to cover {audio_duration:.0f}s of audio"
        )

        # Step 3: encode the planned slices in parallel. Hardware encoders
        # serialize on the GPU's encode engine internally, so 2 concurrent
        # jobs is usually enough for NVENC/QSV. libx264 benefits from more.
        cpu = max(int(os.cpu_count() or 4), 2)
        if encoder["name"] in ("h264_nvenc", "h264_qsv", "h264_amf"):
            max_workers = 2
            threads_per_job = max(int(threads), 2)
        else:
            max_workers = max(min(cpu // 2, 4), 1)
            threads_per_job = max(cpu // max_workers, 1)

        normalized_clips: List[Optional[str]] = [None] * len(planned)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for i, (src, start, dur) in enumerate(planned):
                dst = os.path.join(work_dir, f"norm-{i:05d}.mp4")
                fut = pool.submit(
                    _encode_slice,
                    src,
                    dst,
                    start,
                    dur,
                    width,
                    height,
                    threads_per_job,
                    encoder,
                )
                futures[fut] = (i, dst)
            done = 0
            for fut in as_completed(futures):
                i, dst = futures[fut]
                ok = False
                try:
                    ok = bool(fut.result())
                except Exception as exc:
                    logger.warning(f"slice {i} encode raised: {exc}")
                if ok:
                    normalized_clips[i] = dst
                done += 1
                if done % 10 == 0 or done == len(futures):
                    logger.info(
                        f"long-form combine: encoded {done}/{len(futures)} slices"
                    )

        playlist = [c for c in normalized_clips if c]
        if not playlist:
            raise RuntimeError("no normalized clips produced; cannot assemble video")

        list_path = os.path.join(work_dir, "concat-list.txt")
        with open(list_path, "w", encoding="utf-8") as fp:
            for clip in playlist:
                fp.write(
                    f"file '{_escape_ffmpeg_concat_path(os.path.abspath(clip))}'\n"
                )

        # Stream-copy concat: all slices share encoder settings.
        cmd = [
            get_ffmpeg_binary(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            combined_video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "ffmpeg concat failed").strip()
            )

        return combined_video_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def mux_audio_and_subtitles(
    silent_video: str,
    audio_file: str,
    subtitle_file: str,
    output_file: str,
    threads: int = 4,
) -> str:
    """
    Final ffmpeg pass: take the silent assembled video, mix in the narration,
    burn subtitles in. We re-encode video here (libx264) so the burnt-in
    subtitles bake into the picture; audio is re-encoded to AAC.

    Subtitle burning uses the `subtitles=` filter, which is wider compatible
    than embedded soft subs and works on every player.
    """
    if not os.path.exists(silent_video):
        raise FileNotFoundError(silent_video)
    if not os.path.exists(audio_file):
        raise FileNotFoundError(audio_file)

    vf_chain = []
    if subtitle_file and os.path.exists(subtitle_file):
        # ffmpeg subtitles filter wants forward-slashed paths on Windows.
        normalized_sub = subtitle_file.replace("\\", "/").replace(":", "\\:")
        vf_chain.append(f"subtitles='{normalized_sub}'")

    encoder = _detect_video_encoder()
    # Tighten quality slightly for the final pass (the only re-encode after
    # subtitle burn-in) without going so high it slows us down.
    final_args = list(encoder["args"])
    if encoder["name"] == "libx264":
        if "-crf" in final_args:
            i = final_args.index("-crf")
            final_args[i + 1] = "20"
    elif encoder["name"] == "h264_nvenc":
        if "-cq" in final_args:
            i = final_args.index("-cq")
            final_args[i + 1] = "20"
    elif encoder["name"] == "h264_qsv":
        if "-global_quality" in final_args:
            i = final_args.index("-global_quality")
            final_args[i + 1] = "20"

    cmd = [
        get_ffmpeg_binary(),
        "-y",
        "-i",
        silent_video,
        "-i",
        audio_file,
    ]
    if vf_chain:
        cmd += ["-vf", ",".join(vf_chain)]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *final_args,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # Match runtime to the shorter of (video, audio) — this prevents the
        # final file from having a long tail of silent black frames if the
        # video assembly slightly overshot the narration.
        "-shortest",
        "-threads",
        str(max(int(threads), 1)),
        output_file,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "ffmpeg mux failed").strip()
        )
    return output_file


__all__ = [
    "generate_long_script",
    "tts_long",
    "combine_videos_fast",
    "mux_audio_and_subtitles",
]

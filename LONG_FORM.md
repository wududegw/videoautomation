# moneyPrinterTurbo_Long — long-form & extended-sources edition

This is a downstream fork of [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)
that adds three things on top of the upstream short-form pipeline:

1. **More stock-footage providers** — Coverr, Videvo, Mixkit (best-effort), plus
   a much nicer "local drop-folder" mode for clips you legally licensed
   yourself (Shutterstock / Adobe Stock / Getty / your own camera).
2. **Long-form pipeline** — generate 30-minute to 2-hour videos via chunked
   LLM script generation, chunked TTS with ffmpeg audio concat, and ffmpeg-only
   video assembly that skips MoviePy's per-clip re-encode bottleneck.
3. The original short-form pipeline is **untouched**. Long-form is opt-in via
   a checkbox in the WebUI (or `long_form=True` in the API).

The original README and quick-start (Docker, `uv sync`, etc.) still apply.

---

## 1. New / improved video sources

| Source | Status | License / cost | Notes |
|---|---|---|---|
| `pexels` | unchanged | free, royalty-free | original upstream provider |
| `pixabay` | unchanged | free, royalty-free | original upstream provider |
| `coverr` | **NEW** | free, API key required | 50 req/hr demo, 2000/hr paid. Free API at [coverr.co/developers](https://coverr.co/developers). **Attribution required** (Coverr logo + link). |
| `videvo` | **NEW** | partner API, mostly free clips selectable | Needs Videvo partner approval. Override `videvo_base_url` in config if your account uses a different endpoint. |
| `mixkit` | **NEW (best-effort)** | free under Mixkit license | No public API — uses HTML scraping. Brittle, may break without warning. Review the [Mixkit license](https://mixkit.co/license/) before using in production. |
| `local` | **improved** | depends on the clips you supply | Supports either WebUI upload **or** a config-driven drop folder. |

### Setting API keys

Edit `config.toml`:

```toml
[app]
pexels_api_keys = ["YOUR_PEXELS_KEY"]
pixabay_api_keys = ["YOUR_PIXABAY_KEY"]
coverr_api_keys = ["YOUR_COVERR_KEY"]
videvo_api_keys = ["YOUR_VIDEVO_KEY"]
# videvo_base_url = "https://www.videvo.net/api/v1/search/"
```

You can put multiple keys in any list — the pipeline round-robins them across
search calls to dodge per-key rate limits.

### Mixkit caveat (please read)

Mixkit doesn't publish an API. The `mixkit` provider parses their public
search-results HTML to find `mp4` URLs. This works today but:

- Mixkit can change their HTML at any time and break the scraper. The provider
  is defensive — if scraping fails, it returns an empty list and the task
  falls through to whatever other source you have configured.
- The clips themselves are free to use under the [Mixkit license](https://mixkit.co/license/),
  but the license forbids redistribution, re-selling, or some commercial uses.
  Read it before publishing the output.
- We use polite headers, single requests per term, and **no** parallel scraping.
  Don't crank up the search-term count past ~10 or you risk being rate-limited.

If you need reliable bulk footage, prefer Coverr (real API) or a local drop
folder of clips you own.

---

## 2. Local drop-folder mode

The original "local" source required you to upload every clip through the
Streamlit UI. The Long edition keeps that, but **adds** a drop-folder fallback:

1. Put your owned/licensed clips into one folder (any depth):

   ```
   D:\stock-footage\earth-science\
   ├── ocean.mp4
   ├── lava.mov
   ├── nebula.mp4
   └── sub-folders\
       └── microscope.mkv
   ```

2. Tell `config.toml` where it is:

   ```toml
   [app]
   material_directory = "D:\\stock-footage\\earth-science"
   ```

   (Double backslashes for Windows TOML paths. On macOS/Linux just use `/`.)

3. In the WebUI, pick **Local file** as the video source, **leave the upload
   widget empty**, and click Generate.

Every `mp4`, `mov`, `mkv`, `webm` under the folder is treated as material.
Sub-folders are walked recursively. Files smaller than 1 byte are skipped.
Resolution is checked by `preprocess_video` later, so clips below 480p are
rejected with a warning.

You can override the folder per-run by typing a different path into the
"drop-folder path" field that appears under the upload widget.

### Why this matters for paid stock services

Shutterstock, Adobe Stock, Getty, etc. all have APIs, but every download
counts against a per-clip license, typically $50–$500 per video clip. Running
an automated pipeline that re-downloads clips on each generation would burn
license credits very fast. The proper workflow is:

1. License the clips you want **once**, store them locally.
2. Point `material_directory` at that folder.
3. Generate as many videos as you want from the same library.

This sidesteps both the cost issue and the API gating.

---

## 3. Long-form pipeline

Enable in the WebUI by ticking **"Generate long-form video"** and setting the
**target length in minutes**. Or via the API:

```python
from app.models.schema import VideoParams
params = VideoParams(
    video_subject="The full history of the Roman Empire",
    long_form=True,
    target_minutes=60,
    video_source="local",       # strongly recommended for long-form
    video_clip_duration=6,
)
```

### What changes in the pipeline

| Stage | Short-form (default) | Long-form (`long_form=True`) |
|---|---|---|
| Script | Single LLM call, ≤ ~1500 words | Chapter-by-chapter LLM calls via `long_form.generate_long_script`. Each chapter ≈ 5 min × 150 wpm ≈ 750 words. Up to 24 chapters. |
| Search terms | 5 terms | `≈ target_minutes / 5 × 3` terms (min 10, max 30) |
| TTS | Single Edge-TTS stream | `long_form.tts_long`: splits at paragraphs/sentences, ≤ 4000 chars/chunk. Each chunk is its own Edge-TTS call. Chunks are concatenated with the ffmpeg concat demuxer. |
| Subtitles | Edge word boundaries (fast) | Forced to **whisper** because Edge word boundaries only line up with the first chunk after concat. |
| Video assembly | MoviePy: opens every clip, re-encodes with `write_videofile`, then ffmpeg-concat | `long_form.combine_videos_fast`: slices each source clip with ffmpeg, normalizes once to a fixed codec/resolution, then concat-demuxer stream-copy. No MoviePy frame loops. |
| Final mux + subtitle burn | MoviePy `CompositeVideoClip` | `long_form.mux_audio_and_subtitles`: pure ffmpeg, libx264 + AAC, `subtitles=` filter for burn-in. |

### Realistic expectations for long-form

**Wall-clock time** (rough, on a modern laptop CPU):

| Output length | Pipeline time |
|---|---|
| 15 min | ~10–20 min (chunked TTS dominates) |
| 30 min | ~20–40 min |
| 60 min | ~45–90 min |
| 2 hours | 2–4 hours; risk of OOM if `material_directory` is huge |

**Disk space:** budget at least 3× the final video size during rendering for
the silent-reel, narration MP3, and temporary normalized clips. A 2-hour
1080p video at our default CRF will be ~3-6 GB final + ~10–15 GB scratch.

**LLM tokens:** with 24 chapters × ~1500 prompt + completion tokens ≈ 36k
tokens total per script. Cost on DeepSeek/Pollinations is negligible; on
GPT-4o-class models it's a few dollars per script.

**Material variety:** the biggest practical issue. Pexels/Pixabay return at
most ~50 unique clips per search term, and many overlap. A 1-hour video at
5-second clips needs ~720 cuts. Even with 30 search terms, expect heavy
repetition. **Use `material_directory` with a hand-curated folder of
hundreds of clips for the best results.**

### Whisper subtitle requirement

Whisper-large-v3 (≈3 GB) is needed for long-form. Download it once (see the
upstream README under "字幕生成") into `models/whisper-large-v3/`. Then in
`config.toml`:

```toml
[app]
subtitle_provider = "whisper"
[whisper]
model_size = "large-v3"
device = "CPU"           # or "CUDA" if you have a GPU
compute_type = "int8"    # "float16" on GPU
```

A GPU drastically helps here: CPU whisper on 1 hour of audio takes 20+
minutes; on a recent NVIDIA card it's 2–3 minutes.

### Known limitations

1. **Subtitle burn-in is hard** to undo. If you want soft subtitles instead
   of burnt-in ones, edit `long_form.mux_audio_and_subtitles` to add
   `-c:s mov_text` and `-map 2:s:0` rather than the `subtitles=` video
   filter.
2. **No background music yet** in the long-form path. The `mux_audio_and_subtitles`
   step only mixes narration. If you need BGM in long-form, add a third
   `-i` input + an `amix` filter — easy follow-up, but kept out of v1 to
   keep the change small.
3. **Multi-video output disabled.** Long-form forces `video_count=1` because
   producing N parallel 1-hour videos in a single task is almost never what
   the user wanted, and it would multiply disk usage by N.
4. **No transitions** in the long-form path. Hard cuts only. Transitions are
   feasible (xfade filter chain) but were skipped to keep the encoder
   pipeline simple and reliable.

---

## 4. Quick recipe — fully self-hosted hour-long documentary

1. Download Whisper-large-v3 into `models/whisper-large-v3/`.
2. License or download ~200 stock clips related to your topic; put them in
   `D:\stock-footage\my-topic\`.
3. In `config.toml`:
   ```toml
   [app]
   llm_provider = "pollinations"           # free, no key
   pollinations_base_url = "https://text.pollinations.ai/openai"
   subtitle_provider = "whisper"
   video_source = "local"
   material_directory = "D:\\stock-footage\\my-topic"
   ```
4. Launch the WebUI:
   ```powershell
   .\.venv\Scripts\python.exe -m streamlit run .\webui\Main.py --browser.gatherUsageStats=False
   ```
5. Tick **Generate long-form video**, set **Target length** to `60`, pick
   your subject, hit Generate.
6. Walk away for ~45 minutes. Check `storage\tasks\<task-id>\final-1.mp4`.

---

## 5. Architecture notes for hackers

- All new code lives in:
  - `app/services/long_form.py` — chunked LLM, chunked TTS, ffmpeg helpers.
  - `app/services/material.py` — `search_videos_coverr/videvo/mixkit`, `discover_local_materials`.
  - `app/services/task.py` — small `_is_long_form(params)` branches.
  - `app/models/schema.py` — `long_form: bool`, `target_minutes: float`.
  - `webui/Main.py` — new dropdown entries, long-form toggle, drop-folder UI.
- The short-form path imports nothing from `long_form`, so the new module's
  ffmpeg behavior cannot regress the existing pipeline.
- `combine_videos_fast` is intentionally **stream-copy only** in the concat
  step; the per-clip encode cost is the only place we pay for libx264 in the
  long-form path. This is what makes hour-long output feasible.
- `mux_audio_and_subtitles` re-encodes video one time to burn subtitles. If
  you don't need burnt-in subtitles, change that pass to `-c:v copy` and the
  final render becomes nearly free.

If you find a bug, the smallest reproduction is to call the helpers directly:

```python
from app.services import long_form

# script chunking
chunks = long_form._split_for_tts("paragraph 1.\n\nparagraph 2.", max_chars=20)

# tts only
long_form.tts_long(text=open("script.txt").read(),
                   voice_name="en-AU-NatashaNeural",
                   voice_rate=1.0, voice_file="out.mp3")

# video only
long_form.combine_videos_fast(
    combined_video_path="reel.mp4",
    video_paths=["clip1.mp4", "clip2.mp4", "clip3.mp4"],
    audio_duration=600.0,
    width=1920, height=1080,
    max_clip_duration=6,
)
```

That's it. PRs welcome.

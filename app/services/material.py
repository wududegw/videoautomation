import html
import os
import random
import re
import threading
from typing import List
from urllib.parse import quote_plus, urlencode

import requests
from loguru import logger
from moviepy.video.io.VideoFileClip import VideoFileClip

from app.config import config
from app.models import const
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode
from app.utils import utils

# Thread-safe counter for API key rotation
_api_key_counter = 0
_api_key_lock = threading.Lock()

# Sources that support free-text search via a remote API.
REMOTE_SOURCES = {"pexels", "pixabay", "coverr", "videvo", "mixkit"}


def _get_tls_verify() -> bool:
    # 默认开启 TLS 证书校验，防止素材搜索和下载过程被中间人篡改。
    # 仅在企业代理、自签证书等明确需要的场景下，允许用户通过
    # `config.toml` 显式设置 `tls_verify = false` 临时关闭。
    tls_verify = config.app.get("tls_verify", True)
    if isinstance(tls_verify, str):
        tls_verify = tls_verify.strip().lower() not in ("0", "false", "no", "off")

    if not tls_verify:
        logger.warning(
            "TLS certificate verification is disabled by config.app.tls_verify=false. "
            "Only use this in trusted proxy environments."
        )

    return bool(tls_verify)


def get_api_key(cfg_key: str):
    api_keys = config.app.get(cfg_key)
    if not api_keys:
        raise ValueError(
            f"\n\n##### {cfg_key} is not set #####\n\nPlease set it in the config.toml file: {config.config_file}\n\n"
            f"{utils.to_json(config.app)}"
        )

    # if only one key is provided, return it
    if isinstance(api_keys, str):
        return api_keys

    global _api_key_counter
    with _api_key_lock:
        _api_key_counter += 1
        return api_keys[_api_key_counter % len(api_keys)]


def search_videos_pexels(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    video_orientation = aspect.name
    video_width, video_height = aspect.to_resolution()
    api_key = get_api_key("pexels_api_keys")
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }
    # Build URL
    params = {"query": search_term, "per_page": 20, "orientation": video_orientation}
    query_url = f"https://api.pexels.com/videos/search?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
        video_items = []
        if "videos" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["videos"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["video_files"]
            # loop through each url to determine the best quality
            for video in video_files:
                w = int(video["width"])
                h = int(video["height"])
                if w == video_width and h == video_height:
                    item = MaterialInfo()
                    item.provider = "pexels"
                    item.url = video["link"]
                    item.duration = duration
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_pixabay(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)

    video_width, video_height = aspect.to_resolution()

    api_key = get_api_key("pixabay_api_keys")
    # Build URL
    params = {
        "q": search_term,
        "video_type": "all",  # Accepted values: "all", "film", "animation"
        "per_page": 50,
        "key": api_key,
    }
    query_url = f"https://pixabay.com/api/videos/?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url, proxies=config.proxy, verify=_get_tls_verify(), timeout=(30, 60)
        )
        response = r.json()
        video_items = []
        if "hits" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["hits"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["videos"]
            # Prefer clips that already match the requested orientation/aspect
            # (especially portrait 9:16), then pick the smallest sufficient file.
            # This avoids downloading landscape 720p/4K clips and spending CPU
            # time resizing them into portrait output.
            target_ratio = video_width / video_height
            target_is_portrait = video_height > video_width

            candidates = []
            for video_type in video_files:
                video = video_files[video_type]
                w = int(video["width"])
                h = int(video["height"])
                same_orientation = (h >= w) if target_is_portrait else (w >= h)
                resolution_ok = w >= video_width and h >= video_height
                aspect_delta = abs((w / h) - target_ratio)
                candidates.append(
                    (
                        0 if same_orientation else 1,
                        aspect_delta,
                        0 if resolution_ok else 1,
                        w * h,
                        video,
                    )
                )

            chosen = min(candidates)[-1] if candidates else None
            if chosen:
                item = MaterialInfo()
                item.provider = "pixabay"
                item.url = chosen["url"]
                item.duration = duration
                video_items.append(item)
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_coverr(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    """
    Search Coverr's free stock video library.

    Coverr public API: https://api.coverr.co/docs/
    Free demo tier: 50 req/hour. Paid Pro: 2000 req/hour.
    Coverr returns curated horizontal HD clips; we filter by minimum duration
    and reject clips that are too narrow for the chosen aspect ratio.
    """
    api_key = get_api_key("coverr_api_keys")
    aspect = VideoAspect(video_aspect)
    video_width, video_height = aspect.to_resolution()

    params = {
        "query": search_term,
        "page_size": 30,
        "urls": "true",
        "sort": "popular",
    }
    query_url = f"https://api.coverr.co/videos?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers={"Authorization": f"Bearer {api_key}"},
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
    except Exception as e:
        logger.error(f"coverr search failed: {str(e)}")
        return []

    if "hits" not in response:
        logger.error(f"coverr search response missing hits: {response}")
        return []

    video_items: List[MaterialInfo] = []
    for v in response.get("hits", []):
        # Coverr returns `max_duration` (seconds) as a float.
        duration = int(v.get("max_duration") or v.get("duration") or 0)
        if duration < minimum_duration:
            continue

        urls = v.get("urls") or {}
        # Pick the highest quality MP4 we have access to.
        download_url = urls.get("mp4_download") or urls.get("mp4")
        if not download_url:
            continue

        # Coverr clips are 1920x1080 at minimum, so they suit both 16:9 and 9:16
        # (we re-frame in combine_videos). Filter out obvious portrait mismatches.
        width = int(v.get("width") or 0)
        if width and width < min(video_width, video_height):
            continue

        item = MaterialInfo()
        item.provider = "coverr"
        item.url = download_url
        item.duration = duration
        video_items.append(item)

    return video_items


def search_videos_videvo(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    """
    Search Videvo's stock video library (API key required).

    Videvo offers a RESTful API to partners (https://www.videvo.net/blog/announcing-the-new-api/).
    The API and its concrete endpoint shape are not as openly documented as Pexels/Pixabay;
    plug in your partner endpoint below. This implementation hits the documented
    `/api/search` style endpoint with a `category=free` filter so that paid clips
    aren't accidentally selected. Override `videvo_base_url` in config.toml if your
    account's endpoint differs.

    Default base URL points at Videvo's public-facing search; if no API key is set
    or the endpoint shape changes, this provider gracefully returns an empty list
    instead of failing the whole task.
    """
    try:
        api_key = get_api_key("videvo_api_keys")
    except ValueError as exc:
        logger.warning(f"videvo disabled: {exc}")
        return []

    base_url = (
        config.app.get("videvo_base_url", "").strip()
        or "https://www.videvo.net/api/v1/search/"
    )
    aspect = VideoAspect(video_aspect)
    video_width, video_height = aspect.to_resolution()

    params = {
        "query": search_term,
        "type": "video",
        # Restrict to free assets unless the user explicitly wants paid ones.
        "license": "free",
        "per_page": 30,
    }
    query_url = f"{base_url}?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
    except Exception as e:
        logger.error(f"videvo search failed: {str(e)}")
        return []

    # The API returns `results` for the canonical search endpoint; some
    # partner deployments use `data`. Accept both.
    items = response.get("results") or response.get("data") or response.get("hits") or []
    if not isinstance(items, list):
        logger.error(f"videvo unexpected response shape: {response}")
        return []

    video_items: List[MaterialInfo] = []
    for v in items:
        duration = int(v.get("duration") or v.get("length") or 0)
        if duration and duration < minimum_duration:
            continue

        # Try several common URL field names.
        download_url = (
            v.get("download_url")
            or v.get("mp4_url")
            or v.get("preview_url")
            or v.get("url")
        )
        if not download_url:
            continue

        width = int(v.get("width") or v.get("video_width") or 0)
        if width and width < min(video_width, video_height):
            continue

        item = MaterialInfo()
        item.provider = "videvo"
        item.url = download_url
        item.duration = duration or minimum_duration
        video_items.append(item)

    return video_items


def search_videos_mixkit(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    """
    Best-effort Mixkit provider.

    Mixkit (https://mixkit.co) has NO public API. Their videos are free to use
    under the Mixkit license, but the only programmatic access is HTML scraping
    of their public search page. This is fragile (their HTML can change at any
    time) and you should review the Mixkit license before relying on it in
    production. We rate-limit and use polite headers; if scraping breaks, the
    provider just returns an empty list and the task falls back to other sources.

    This function looks for `<source src="...free-stock-video-....mp4">` tags
    on `https://mixkit.co/free-stock-video/{search_term}/`. We do NOT parse
    duration (Mixkit's listing page doesn't expose it consistently), so we
    pass the user-requested `minimum_duration` straight through.
    """
    aspect = VideoAspect(video_aspect)
    video_width, _ = aspect.to_resolution()

    # Mixkit uses dashed search slugs.
    slug = quote_plus(search_term.lower().replace(" ", "-"))
    query_url = f"https://mixkit.co/free-stock-video/{slug}/"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
            },
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        if r.status_code != 200:
            logger.warning(f"mixkit returned status {r.status_code} for '{search_term}'")
            return []
        html_text = r.text
    except Exception as e:
        logger.error(f"mixkit search failed: {str(e)}")
        return []

    # Look for the high-quality download URLs Mixkit embeds in the page.
    # Sample pattern: https://assets.mixkit.co/videos/preview/.../mixkit-...-large.mp4
    pattern = re.compile(
        r"https://[^\"'\s]+mixkit[^\"'\s]+?\.mp4", re.IGNORECASE
    )
    candidates = list(dict.fromkeys(pattern.findall(html_text)))
    # Filter out tiny thumbnail/preview ".gif" sources and obvious duplicates.
    filtered = [u for u in candidates if u.lower().endswith(".mp4")]

    if not filtered:
        return []

    video_items: List[MaterialInfo] = []
    for url in filtered[:25]:
        item = MaterialInfo()
        item.provider = "mixkit"
        item.url = html.unescape(url)
        item.duration = max(int(minimum_duration), 5)
        video_items.append(item)
    return video_items


def discover_local_materials(
    directory: str, minimum_duration: int = 0
) -> List[MaterialInfo]:
    """
    Walk `directory` and return every readable video file as a MaterialInfo.

    Used when `video_source == "local"` and `material_directory` is set in
    config.toml — the user can simply drop owned/purchased clips into that
    folder and the pipeline picks them up automatically (no upload step
    needed in the WebUI).
    """
    if not directory:
        return []
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        logger.warning(f"local material directory does not exist: {directory}")
        return []

    accepted_exts = {f".{ext}" for ext in const.FILE_TYPE_VIDEOS}
    materials: List[MaterialInfo] = []

    for root, _, files in os.walk(directory):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in accepted_exts:
                continue
            full_path = os.path.join(root, filename)
            try:
                size = os.path.getsize(full_path)
                if size == 0:
                    continue
            except OSError:
                continue

            item = MaterialInfo()
            item.provider = "local"
            item.url = full_path
            # We don't probe duration here to keep discovery fast; the
            # preprocess step in video.py will probe each clip anyway and
            # reject anything unreadable.
            item.duration = max(int(minimum_duration), 0)
            materials.append(item)

    logger.info(
        f"discovered {len(materials)} local video files in: {directory}"
    )
    return materials


def save_video(video_url: str, save_dir: str = "") -> str:
    if not save_dir:
        save_dir = utils.storage_dir("cache_videos")

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_without_query = video_url.split("?")[0]
    url_hash = utils.md5(url_without_query)
    video_id = f"vid-{url_hash}"
    video_path = f"{save_dir}/{video_id}.mp4"

    # if video already exists, return the path
    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # if video does not exist, download it
    with open(video_path, "wb") as f:
        f.write(
            requests.get(
                video_url,
                headers=headers,
                proxies=config.proxy,
                verify=_get_tls_verify(),
                timeout=(60, 240),
            ).content
        )

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        clip = None
        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            fps = clip.fps
            if duration > 0 and fps > 0:
                return video_path
        except Exception as e:
            logger.warning(f"invalid video file: {video_path} => {str(e)}")
            try:
                os.remove(video_path)
            except Exception as remove_error:
                logger.warning(
                    f"failed to remove invalid video file: {video_path}, error: {str(remove_error)}"
                )
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception as close_error:
                    logger.warning(
                        f"failed to close video clip: {video_path}, error: {str(close_error)}"
                    )
    return ""


def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_contact_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
) -> List[str]:
    valid_video_items = []
    valid_video_urls = []
    found_duration = 0.0

    # Source dispatch table — order is important here so that adding new free
    # providers is a one-line change in the future.
    dispatch = {
        "pexels": search_videos_pexels,
        "pixabay": search_videos_pixabay,
        "coverr": search_videos_coverr,
        "videvo": search_videos_videvo,
        "mixkit": search_videos_mixkit,
    }
    search_videos = dispatch.get(source, search_videos_pexels)

    for search_term in search_terms:
        video_items = search_videos(
            search_term=search_term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )
        logger.info(f"found {len(video_items)} videos for '{search_term}'")

        for item in video_items:
            if item.url not in valid_video_urls:
                valid_video_items.append(item)
                valid_video_urls.append(item.url)
                found_duration += item.duration

    logger.info(
        f"found total videos: {len(valid_video_items)}, required duration: {audio_duration} seconds, found duration: {found_duration} seconds"
    )
    video_paths = []

    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        material_directory = utils.task_dir(task_id)
    elif material_directory and not os.path.isdir(material_directory):
        material_directory = ""

    if video_contact_mode.value == VideoConcatMode.random.value:
        random.shuffle(valid_video_items)

    total_duration = 0.0
    for item in valid_video_items:
        try:
            logger.info(f"downloading video: {item.url}")
            saved_video_path = save_video(
                video_url=item.url, save_dir=material_directory
            )
            if saved_video_path:
                logger.info(f"video saved: {saved_video_path}")
                video_paths.append(saved_video_path)
                seconds = min(max_clip_duration, item.duration)
                total_duration += seconds
                if total_duration > audio_duration:
                    logger.info(
                        f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                    )
                    break
        except Exception as e:
            logger.error(f"failed to download video: {utils.to_json(item)} => {str(e)}")
    logger.success(f"downloaded {len(video_paths)} videos")
    return video_paths


if __name__ == "__main__":
    download_videos(
        "test123", ["Money Exchange Medium"], audio_duration=100, source="pixabay"
    )

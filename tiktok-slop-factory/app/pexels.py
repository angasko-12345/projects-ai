"""
Pexels API client for stock footage.

Pexels is free with a 200 requests/hour quota, so every call here is made to
count: searches are capped, results are cached on disk by video id, and
downloads are validated before being trusted.

Fixes over the original implementation:

* ``Any`` was used in annotations but never imported. That is a ``NameError``
  the moment anything reflects on the annotations (and on Python 3.14 lazy
  annotation evaluation it blows up under ``get_type_hints``).
* ``video_files`` entries use ``link``, not ``url``. The old fallback chain
  silently produced ``None`` and then "No video URL".
* Pexels landscape results are now handled: a landscape clip is letterbox
  cropped by the renderer rather than failing the crop filter outright.
* 429/5xx responses are retried with backoff instead of killing the batch.
* Downloads stream to a temp file and are validated for mp4 content, so a
  truncated or HTML error page is never cached as a "valid" clip.
"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_pexels_api_key, get_project_root


PEXELS_BASE = "https://api.pexels.com/v1"

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

# A real mp4 always contains one of these near the start of the file.
_MP4_MARKERS = (b"ftyp", b"moov", b"mdat")


class PexelsError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Dict[str, Any]:
    """GET a Pexels endpoint with retries on rate limits and server faults."""
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{PEXELS_BASE}{path}{qs}"
    last: Optional[PexelsError] = None

    for attempt in range(MAX_ATTEMPTS):
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": get_pexels_api_key(),
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            if not body.strip():
                raise PexelsError("Pexels returned an empty body", retryable=True)
            return json.loads(body)
        except urllib.error.HTTPError as e:
            try:
                err = e.read().decode("utf-8", errors="replace")
            except Exception:
                err = ""
            status = getattr(e, "code", None)
            last = PexelsError(
                f"Pexels HTTP {status}: {err.strip()}",
                status=status,
                retryable=status in _RETRYABLE_STATUS,
            )
        except PexelsError as e:
            last = e
        except json.JSONDecodeError as e:
            raise PexelsError(f"Pexels returned non-JSON: {e}") from None
        except urllib.error.URLError as e:
            last = PexelsError(f"Pexels network error: {e.reason}", retryable=True)
        except OSError as e:
            last = PexelsError(f"Pexels network error: {e}", retryable=True)

        if not last.retryable or attempt == MAX_ATTEMPTS - 1:
            raise last
        time.sleep((2**attempt) + random.uniform(0, 1))

    raise last or PexelsError("Pexels request failed")


def _pick_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Choose the best usable mp4 rendition from a Pexels video.

    Prefers a portrait file near 1080x1920, then any tall file, then the
    highest-resolution file available. HLS/stream manifests are excluded
    because they are not downloadable media.
    """
    mp4s = [
        f
        for f in files
        if f.get("file_type") == "video/mp4" and f.get("link") and (f.get("width") or 0)
    ]
    if not mp4s:
        return None

    def score(f: Dict[str, Any]) -> tuple:
        w = f.get("width") or 0
        h = f.get("height") or 0
        portrait = h > w
        # Prefer 1080-wide portrait, then tall, then larger overall.
        exact = 1 if (portrait and w == 1080) else 0
        return (exact, 1 if portrait else 0, w * h)

    return max(mp4s, key=score)


def search_videos(query: str, per_page: int = 3) -> List[Dict[str, Any]]:
    """Search Pexels for video clips matching ``query``.

    Requests portrait orientation first; on an empty result it retries without
    the orientation filter so a landscape clip can still be used (the renderer
    crops it to vertical).
    """
    query = (query or "").strip()
    if not query:
        return []

    per_page = max(1, min(int(per_page), 15))
    res = _get(
        "/search/videos",
        {"query": query, "per_page": str(per_page), "orientation": "portrait"},
    )
    videos = res.get("videos") or []

    if not videos:
        # Fall back to any orientation rather than failing the whole video.
        res = _get("/search/videos", {"query": query, "per_page": str(per_page)})
        videos = res.get("videos") or []

    out: List[Dict[str, Any]] = []
    for v in videos:
        if not isinstance(v, dict):
            continue
        best = _pick_file(v.get("video_files") or [])
        if not best:
            continue
        user = v.get("user") or {}
        out.append(
            {
                "id": v.get("id"),
                "url": best.get("link"),
                "width": best.get("width"),
                "height": best.get("height"),
                "duration": v.get("duration"),
                "user": user.get("name") if isinstance(user, dict) else None,
                "page_url": v.get("url"),
            }
        )
    return out


def cache_dir() -> Path:
    d = get_project_root() / "output" / "footage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _looks_like_mp4(path: Path) -> bool:
    """Cheap sanity check that a downloaded file is really video."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if head[:12].startswith(b"ftyp"):
        return True
    if any(marker in head for marker in _MP4_MARKERS):
        return True
    # QuickTime/older containers put ftyp slightly further in.
    try:
        with open(path, "rb") as f:
            return b"ftyp" in f.read(512)
    except OSError:
        return False


def _is_cached(path: Path) -> bool:
    """A cache entry is trusted only if it is big enough and really mp4."""
    try:
        return path.is_file() and path.stat().st_size > 10_000 and _looks_like_mp4(path)
    except OSError:
        return False


def download_video(video_info: Dict[str, Any]) -> Path:
    """Download a clip to the cache and return its path.

    Downloads to a temp file first and only renames on success, so an
    interrupted transfer can never poison the cache with a truncated file
    that later gets "reused" as valid footage.
    """
    url = video_info.get("url")
    if not url:
        raise PexelsError("No video URL on Pexels result")
    vid = str(video_info.get("id") or "vid")
    safe_id = "".join(c for c in vid if c.isalnum() or c in "-_")[:32] or "vid"

    path = cache_dir() / f"{safe_id}.mp4"
    if _is_cached(path):
        return path
    # Drop an invalid leftover so it cannot be mistaken for a cache hit.
    if path.exists():
        path.unlink()

    tmp = path.with_suffix(".part")
    last: Optional[PexelsError] = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "tiktok-slop-factory/1.0"}
            )
            with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)

            if not _is_cached(tmp):
                raise PexelsError("Downloaded file is not a usable mp4", retryable=True)

            tmp.replace(path)
            return path
        except urllib.error.HTTPError as e:
            status = getattr(e, "code", None)
            last = PexelsError(
                f"Pexels download HTTP {status}",
                status=status,
                retryable=status in _RETRYABLE_STATUS,
            )
        except (PexelsError, OSError, urllib.error.URLError) as e:
            last = e if isinstance(e, PexelsError) else PexelsError(f"Failed to download video: {e}")
            last.retryable = True
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep((2**attempt) + random.uniform(0, 1))

    raise last or PexelsError("Failed to download video")

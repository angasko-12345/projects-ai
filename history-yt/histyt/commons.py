"""Public-domain image fetch from Wikimedia Commons, license-gated.

The fetch only accepts licenses the project already allows. It does not judge
historical authenticity: a period-wrong image can still pass this gate, so the
render review stays with a human.
"""
import hashlib
import html
import json
import os
import re
import time

import requests

from . import scenes as scenes_mod
from .state import PipelineError
from .visuals import hash_distance, image_signature, write_assets

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "histyt/0.1.0 (personal history pipeline)"
MAX_BYTES = 15_000_000

# Defaults for per-scene resolution; every one is overridable under `visuals`.
MIN_WIDTH = 1600
MIN_HEIGHT = 900
MIN_HASH_DISTANCE = 12
MAX_DOWNLOADS_PER_SCENE = 3
HISTORY_WINDOW = 10

_LICENSE_MAP = {
    "public domain": "public domain",
    "pd": "public domain",
    "pd-us": "public domain",
    "cc0": "cc0",
    "cc by 4.0": "cc-by",
    "cc by 3.0": "cc-by",
    "cc by 2.0": "cc-by",
    "us federal work": "us federal work",
}


def _clean_html(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_license(raw):
    """Map a Commons LicenseShortName to an allowed license, else None."""
    text = _clean_html(raw).lower().strip(" .")
    if text in _LICENSE_MAP:
        return _LICENSE_MAP[text]
    return None


def search_images(query, limit=6, timeout=30):
    """Return candidate dicts with url, page_url, title, license and attribution."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6,
        "gsrlimit": max(1, min(int(limit), 20)),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|sha1",
        "iiurlwidth": 1920,
    }
    try:
        resp = requests.get(COMMONS_API, params=params,
                            headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PipelineError(f"Commons search failed for {query!r}: {exc}") from exc
    pages = ((data.get("query") or {}).get("pages") or {}).values()
    candidates = []
    for page in sorted(pages, key=lambda p: p.get("index", 99)):
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata") or {}
        license_name = (metadata.get("LicenseShortName") or {}).get("value", "")
        license_id = normalize_license(license_name)
        if not license_id:
            continue
        url = info.get("thumburl") or info.get("url") or ""
        if not url.startswith("https://"):
            continue
        candidates.append({
            "title": page.get("title", ""),
            "url": url,
            "page_url": info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{page.get('title', '')}",
            "license": license_id,
            "license_name": _clean_html(license_name),
            "attribution": _clean_html((metadata.get("Artist") or {}).get("value", ""))[:200],
            "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0),
            "file_sha1": info.get("sha1") or "",
        })
    return candidates


def _extension(url, content_type):
    ctype = (content_type or "").split(";")[0].strip().lower()
    known = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if ctype in known:
        return known[ctype]
    match = re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", url.lower())
    return f".{match.group(1)}" if match else ".jpg"


def download_image(url, dest_prefix, timeout=60, retries=2):
    """Download one image with https, content-type and size checks.

    Wikimedia rate-limits burst fetches (HTTP 429), so a scene run that resolves
    28 images back to back will hit it. One short backoff per request is enough;
    callers still treat a final failure as that candidate being unusable.
    """
    if not url.startswith("https://"):
        raise PipelineError(f"only https image URLs allowed: {url}")
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                                timeout=timeout, stream=True)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    else:
        raise PipelineError(f"image download failed for {url}: {last_error}")
    content_type = (resp.headers or {}).get("content-type") or ""
    if not content_type.lower().startswith("image/"):
        raise PipelineError(f"not an image content-type for {url}: {content_type or '(missing)'}")
    data = b""
    for chunk in resp.iter_content(65536):
        data += chunk
        if len(data) > MAX_BYTES:
            raise PipelineError(f"image too large for {url} (limit {MAX_BYTES} bytes)")
    if not data:
        raise PipelineError(f"empty image body for {url}")
    dest = dest_prefix + _extension(url, content_type)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def fetch_images(runs_dir, video_id, query, limit=4, timeout=60):
    """Search, license-filter, download, and merge into assets.json."""
    candidates = search_images(query, limit=limit)
    if not candidates:
        raise PipelineError(
            f"no allowed-license images for {query!r}; try a broader query or add assets manually")
    vdir = os.path.join(runs_dir, video_id)
    img_dir = os.path.join(vdir, "images")
    assets_path = os.path.join(vdir, "assets.json")
    assets = []
    if os.path.isfile(assets_path):
        with open(assets_path, encoding="utf-8") as f:
            assets = json.load(f)
    added = []
    for index, candidate in enumerate(candidates, 1):
        prefix = os.path.join(img_dir, f"commons_{index:02d}")
        path = download_image(candidate["url"], prefix, timeout=timeout)
        asset = {
            "path": path,
            "license": candidate["license"],
            "source_url": candidate["page_url"],
            "attribution": candidate["attribution"] or candidate["title"],
            "tier": "primary" if candidate["license"] in ("public domain", "us federal work") else "secondary",
        }
        assets.append(asset)
        added.append(asset)
    write_assets(runs_dir, video_id, assets)
    return added


def _load_assets(vdir):
    path = os.path.join(vdir, "assets.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rank_candidates(candidates, subject="", min_width=MIN_WIDTH, min_height=MIN_HEIGHT):
    """Drop unusable images, then order the rest deterministically.

    Rejects anything under the minimum size and anything portrait, because a
    16:9 crop of a tall image cuts the subject. Ordering is keyword overlap then
    file identity, so the same search always resolves to the same image.
    """
    terms = [word for word in re.findall(r"[a-z0-9]+", (subject or "").lower())
             if len(word) > 2]
    scored = []
    for candidate in candidates:
        width = int(candidate.get("width") or 0)
        height = int(candidate.get("height") or 0)
        if width < int(min_width) or height < int(min_height):
            continue
        if width / max(height, 1) < 1.2:
            continue
        haystack = f"{candidate.get('title', '')} {candidate.get('attribution', '')}".lower()
        overlap = sum(1 for term in terms if term in haystack)
        identity = (candidate.get("file_sha1") or candidate.get("page_url")
                    or candidate.get("url", ""))
        scored.append((-overlap, identity, candidate))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in scored]


def _history_signatures(runs_dir, video_id, window=HISTORY_WINDOW):
    """Signatures from the most recently written runs, so images are not reused."""
    if int(window) <= 0 or not os.path.isdir(runs_dir):
        return []
    entries = []
    for name in os.listdir(runs_dir):
        if name == video_id:
            continue
        path = os.path.join(runs_dir, name, "assets.json")
        if os.path.isfile(path):
            entries.append((os.path.getmtime(path), path))
    entries.sort(reverse=True)
    signatures = []
    for _, path in entries[:int(window)]:
        try:
            with open(path, encoding="utf-8") as f:
                for asset in json.load(f):
                    if asset.get("phash"):
                        signatures.append(asset["phash"])
        except (OSError, ValueError):
            continue
    return signatures


def _asset_for_scene(scene, candidate, query, path, signature):
    attribution = candidate.get("attribution") or candidate.get("title", "")
    license_name = candidate.get("license_name") or candidate.get("license", "")
    with open(path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()
    return {
        "path": path,
        "license": candidate["license"],
        "license_name": license_name,
        "source_url": candidate.get("page_url", ""),
        "attribution": attribution,
        "credit_line": f"{attribution} - {license_name} via Wikimedia Commons",
        "scene_id": scene.get("scene_id", ""),
        "visual_type": scene.get("visual_type", "archive"),
        "provider": "wikimedia-commons",
        "query": query,
        "width": int(candidate.get("width") or 0),
        "height": int(candidate.get("height") or 0),
        "phash": signature,
        "sha256": file_hash,
        "tier": ("primary" if candidate["license"] in ("public domain", "us federal work")
                 else "secondary"),
    }


def fetch_scene_images(runs_dir, video_id, cfg=None, limit=10, timeout=60):
    """Resolve one licensed image per scene, mapped by scene_id.

    Scenes that already have an asset are left alone, so a rerun only fills
    gaps. A scene without a usable candidate is marked ``missing``; it never
    gets a different period's image to fill the slot.

    ``limit`` is candidates per query, not images kept. Commons filters by
    license after retrieval, so a small limit can return nothing while the same
    query with a larger limit finds public-domain results further down the page.
    """
    visuals_cfg = (cfg or {}).get("visuals") or {}
    min_width = int(visuals_cfg.get("min_width") or MIN_WIDTH)
    min_height = int(visuals_cfg.get("min_height") or MIN_HEIGHT)
    min_distance = int(visuals_cfg.get("min_hash_distance") or MIN_HASH_DISTANCE)
    attempts_cap = int(visuals_cfg.get("max_downloads_per_scene") or MAX_DOWNLOADS_PER_SCENE)
    window = visuals_cfg.get("history_window")
    window = HISTORY_WINDOW if window is None else int(window)
    manifest = scenes_mod.load_manifest(runs_dir, video_id)
    if not manifest:
        raise PipelineError(f"scene manifest missing for {video_id}; run: scenes {video_id}")
    vdir = os.path.join(runs_dir, video_id)
    assets = _load_assets(vdir)
    used = [asset.get("phash") for asset in assets if asset.get("phash")]
    used += _history_signatures(runs_dir, video_id, window)
    seen_files = set()
    selected, missing = [], []
    for scene in manifest.get("scenes") or []:
        if scene.get("asset"):
            continue
        queries = [query for query in (scene.get("visual_queries") or []) if query]
        if not queries and scene.get("visual_query"):
            queries = [scene["visual_query"]]
        resolved = None
        for query in queries:
            ranked = rank_candidates(search_images(query, limit=limit),
                                     scene.get("subject", ""), min_width, min_height)
            for candidate in ranked[:attempts_cap]:
                file_key = candidate.get("file_sha1") or candidate.get("url", "")
                if file_key in seen_files:
                    continue
                prefix = os.path.join(vdir, "images",
                                      f"{scene.get('scene_id', 'SC')}_commons")
                try:
                    path = download_image(candidate["url"], prefix, timeout=timeout)
                except PipelineError as exc:
                    # One bad file must not cost the other scenes their images;
                    # the reason stays on the scene for review.
                    scene["asset_error"] = str(exc)[:300]
                    seen_files.add(file_key)
                    continue
                signature = image_signature(path)
                if any(hash_distance(signature, other) < min_distance for other in used):
                    os.remove(path)
                    seen_files.add(file_key)
                    continue
                resolved = _asset_for_scene(scene, candidate, query, path, signature)
                seen_files.add(file_key)
                break
            if resolved:
                break
        if not resolved:
            scene["asset_status"] = "missing"
            missing.append(scene.get("scene_id", ""))
            continue
        scene["asset"] = resolved["path"]
        scene["asset_status"] = "selected"
        scene["visual_query"] = resolved["query"]
        scene.pop("asset_error", None)
        assets.append(resolved)
        used.append(resolved["phash"])
        selected.append(resolved)
    write_assets(runs_dir, video_id, assets)
    scenes_mod.save_manifest(runs_dir, video_id, manifest)
    return {"selected": selected, "missing": missing,
            "scene_count": len(manifest.get("scenes") or [])}

"""Public-domain image fetch from Wikimedia Commons, license-gated.

The fetch only accepts licenses the project already allows. It does not judge
historical authenticity: a period-wrong image can still pass this gate, so the
render review stays with a human.
"""
import html
import json
import os
import re

import requests

from .state import PipelineError
from .visuals import write_assets

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "histyt/0.1.0 (personal history pipeline)"
MAX_BYTES = 15_000_000

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
        "iiprop": "url|extmetadata|size",
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
        })
    return candidates


def _extension(url, content_type):
    ctype = (content_type or "").split(";")[0].strip().lower()
    known = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if ctype in known:
        return known[ctype]
    match = re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", url.lower())
    return f".{match.group(1)}" if match else ".jpg"


def download_image(url, dest_prefix, timeout=60):
    """Download one image with https, content-type and size checks."""
    if not url.startswith("https://"):
        raise PipelineError(f"only https image URLs allowed: {url}")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=timeout, stream=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise PipelineError(f"image download failed for {url}: {exc}") from exc
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

"""Source collection: manual URLs plus Wikipedia/Wikisource/LOC/Met fetch helpers."""
import json
import os
import re
import urllib.parse

import requests

from .state import PipelineError, set_stage

USER_AGENT = "histyt/0.1.0 (personal research pipeline)"
SOURCE_TIERS = ("primary", "secondary", "discovery")
MAX_STORED_TEXT = 200_000


def sources_file(runs_dir, video_id):
    return os.path.join(runs_dir, video_id, "sources.json")


def load_sources(runs_dir, video_id):
    path = sources_file(runs_dir, video_id)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def add_source(runs_dir, video_id, url, title, note, source_id=None,
               tier="secondary", text=""):
    """Append a source with its evidence tier and extracted text.

    `text` is what the claiming pass checks against, so fetch helpers should
    store the page text here rather than only a short note.
    """
    tier = (tier or "secondary").strip().lower()
    if tier not in SOURCE_TIERS:
        raise PipelineError(
            f"unknown source tier {tier!r} (allowed: {', '.join(SOURCE_TIERS)})")
    sources = load_sources(runs_dir, video_id)
    sid = source_id or f"S{len(sources) + 1}"
    sources.append({"id": sid, "url": url, "title": title, "note": note or "",
                    "tier": tier, "text": (text or "")[:MAX_STORED_TEXT]})
    os.makedirs(os.path.join(runs_dir, video_id), exist_ok=True)
    with open(sources_file(runs_dir, video_id), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2, ensure_ascii=False)
    return sid


def research_stage(runs_dir, video_id, sources):
    """Persist sources, filling in tier/text for callers that omit them."""
    normalized = []
    for source in sources:
        entry = dict(source)
        entry.setdefault("tier", "secondary")
        entry.setdefault("text", "")
        tier = (entry.get("tier") or "secondary").lower()
        if tier not in SOURCE_TIERS:
            raise PipelineError(
                f"unknown source tier {tier!r} for {entry.get('id', '?')}")
        entry["tier"] = tier
        normalized.append(entry)
    os.makedirs(os.path.join(runs_dir, video_id), exist_ok=True)
    with open(sources_file(runs_dir, video_id), "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    set_stage(runs_dir, video_id, "research")
    return normalized


def fetch_url(url, timeout=30, max_bytes=2_000_000):
    """Fetch https URL as HTML text; validate status + content-type, cap length.

    Raises PipelineError on: non-https, redirect loops (via requests' own
    redirect history), non-2xx status, non-HTML content-type, or oversize body.
    """
    if not url.startswith("https://"):
        raise PipelineError(f"only https URLs allowed: {url}")
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT},
                            allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise PipelineError(f"fetch failed for {url}: {exc}") from exc
    if len(resp.history) > 5:
        raise PipelineError(f"too many redirects for {url} ({len(resp.history)})")
    content_type = (resp.headers.get("content-type") or "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        raise PipelineError(
            f"fetch returned non-HTML content-type for {url}: {content_type or '(missing)'}")
    text = resp.text
    if len(text) > max_bytes:
        raise PipelineError(
            f"fetched page too large for {url}: {len(text)} chars (limit {max_bytes})")
    return text[:max_bytes]


def fetch_wikipedia(title, timeout=30):
    """Return {'title', 'extract'} for the article; raise PipelineError on HTTP error."""
    params = {
        "action": "query", "prop": "extracts|info", "explaintext": "1",
        "format": "json", "titles": title,
    }
    api = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    try:
        resp = requests.get(api, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PipelineError(f"Wikipedia fetch failed for {title!r}: {exc}") from exc
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        if "extract" in page:
            return {"title": page.get("title", title), "extract": page["extract"][:4000]}
    raise PipelineError(f"Wikipedia article not found: {title!r}")


def _title_from_html(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        raise PipelineError("no <title> tag found in fetched page")
    text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return re.sub(r"\s+", " ", text)[:200]


def fetch_wikisource(url, timeout=30):
    html = fetch_url(url, timeout=timeout)
    return {"title": _title_from_html(html), "text": html[:4000]}


def fetch_loc(url, timeout=30):
    html = fetch_url(url, timeout=timeout)
    return {"title": _title_from_html(html), "text": html[:4000]}


def fetch_met(url, timeout=30):
    html = fetch_url(url, timeout=timeout)
    return {"title": _title_from_html(html), "text": html[:4000]}


def extract_text(html, max_chars=4000):
    """Strip tags from HTML, collapse whitespace, cap length (Priority 1.5)."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

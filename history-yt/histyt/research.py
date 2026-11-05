"""Source collection: manual URLs plus Wikipedia/Wikisource/LOC/Met fetch helpers."""
import json
import os
import re
import urllib.parse

import requests

from .state import PipelineError, set_stage

USER_AGENT = "histyt/0.1.0 (personal research pipeline)"


def sources_file(runs_dir, video_id):
    return os.path.join(runs_dir, video_id, "sources.json")


def load_sources(runs_dir, video_id):
    path = sources_file(runs_dir, video_id)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def add_source(runs_dir, video_id, url, title, note, source_id=None):
    sources = load_sources(runs_dir, video_id)
    sid = source_id or f"S{len(sources) + 1}"
    sources.append({"id": sid, "url": url, "title": title, "note": note or ""})
    os.makedirs(os.path.join(runs_dir, video_id), exist_ok=True)
    with open(sources_file(runs_dir, video_id), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2, ensure_ascii=False)
    return sid


def research_stage(runs_dir, video_id, sources):
    os.makedirs(os.path.join(runs_dir, video_id), exist_ok=True)
    with open(sources_file(runs_dir, video_id), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2, ensure_ascii=False)
    set_stage(runs_dir, video_id, "research")
    return sources


def fetch_url(url, timeout=30):
    """Fetch https URL as text; raise PipelineError on any HTTP error (no silent fallback)."""
    if not url.startswith("https://"):
        raise PipelineError(f"only https URLs allowed: {url}")
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise PipelineError(f"fetch failed for {url}: {exc}") from exc
    return resp.text


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

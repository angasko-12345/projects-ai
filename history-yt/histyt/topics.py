"""Topic registry at runs/topics.jsonl (outside runs/<id>/)."""
import csv
import hashlib
import json
import os
import re

from . import state as state_mod
from .state import PipelineError


def topics_path(runs_dir):
    return os.path.join(runs_dir, "topics.jsonl")


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "video").lower()).strip("-")
    return slug[:40] or "video"


def _unique_id(runs_dir, title, angle):
    h = hashlib.sha256(f"{title}\x00{angle}".encode()).hexdigest()[:6]
    base = f"{slugify(title)}-{h}"
    candidate, n = base, 2
    while os.path.exists(os.path.join(runs_dir, candidate)) or candidate in _existing_ids(runs_dir):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _existing_ids(runs_dir):
    return {t.get("video_id") for t in list_topics(runs_dir)}


def add_topic(runs_dir, title, angle):
    if not title or not title.strip():
        raise PipelineError("topic title is required")
    os.makedirs(runs_dir, exist_ok=True)
    video_id = _unique_id(runs_dir, title.strip(), angle or "")
    state_mod.create(runs_dir, video_id, title.strip(), angle or "")
    entry = {"video_id": video_id, "title": title.strip(), "angle": angle or ""}
    with open(topics_path(runs_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return video_id


def list_topics(runs_dir):
    path = topics_path(runs_dir)
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def import_csv(runs_dir, path):
    """Import topics from CSV (interface; columns: title required, angle optional)."""
    if not os.path.isfile(path):
        raise PipelineError(f"CSV file not found: {path}")
    count = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "title" not in reader.fieldnames:
            raise PipelineError("CSV must have a 'title' column")
        for row in reader:
            if (row.get("title") or "").strip():
                add_topic(runs_dir, row["title"].strip(), (row.get("angle") or "").strip())
                count += 1
    return count

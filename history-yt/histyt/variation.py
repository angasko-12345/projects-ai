"""Variation checks vs history window (titles, thumbnails, templates)."""
import json
import os

from . import state as state_mod
from .thumbnail import hamming


def word_ngrams(title, n):
    words = (title or "").lower().split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def title_similarity(a, b, n=3):
    ga, gb = word_ngrams(a, n), word_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _history(runs_dir, video_id, window):
    rows = []
    if not os.path.isdir(runs_dir):
        return rows
    for vid in os.listdir(runs_dir):
        if vid == video_id:
            continue
        p = state_mod.state_path(runs_dir, vid)
        if os.path.isfile(p):
            try:
                s = state_mod.load(runs_dir, vid)
            except Exception:
                continue
            rows.append(s)
    rows.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return rows[:window]


def variation_check(runs_dir, video_id, cfg):
    state = state_mod.load(runs_dir, video_id)
    thresholds = cfg.get("thresholds") or {}
    window = int(thresholds.get("history_window", 10))
    ngram = int(thresholds.get("title_ngram", 3))
    sim_threshold = float(thresholds.get("title_ngram_similarity", 0.7))
    phash_threshold = int(thresholds.get("thumbnail_phash_distance", 8))
    template_window = int(thresholds.get("template_repeat_window", 5))
    history = _history(runs_dir, video_id, window)
    rows, passed = [], True
    for other in history:
        sim = title_similarity(state.get("title", ""), other.get("title", ""), ngram)
        rows.append({"against": other.get("video_id"), "title_similarity": round(sim, 3),
                     "kind": "title"})
        if sim >= sim_threshold:
            passed = False
    phash = state.get("thumbnail_phash")
    if phash:
        for other in history:
            other_phash = other.get("thumbnail_phash")
            if not other_phash:
                continue
            dist = hamming(phash, other_phash)
            rows.append({"against": other.get("video_id"), "phash_distance": dist,
                         "kind": "thumbnail"})
            if dist < phash_threshold:
                passed = False
    recent_templates = [o.get("template", "") for o in history[:template_window]]
    if state.get("template") and state["template"] in recent_templates:
        rows.append({"against": "template", "template": state["template"],
                     "kind": "template_repeat"})
        passed = False
    report = {"video_id": video_id, "passed": passed, "rows": rows}
    with open(os.path.join(runs_dir, video_id, "variation_report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return rows, passed


def variation_check_stage(runs_dir, video_id, cfg):
    rows, passed = variation_check(runs_dir, video_id, cfg)
    state_mod.set_stage(runs_dir, video_id, "variation_check")
    if not passed:
        from .state import PipelineError
        raise PipelineError("variation check failed; see variation_report.json")
    return rows, passed

"""Shorts planning and extraction (Phase 2, plan + landscape extract).

Deterministic and offline. Windows start/end on chunk boundaries from
durations.json (the same cumulative timing used for captions):

- candidate windows are consecutive chunks within [min_seconds, max_seconds]
- score = hook bonus (question/interrogative first chunk) + named-entity density
  + digits + question marks + sweet-spot length bonus
- greedy selection of highest-scoring non-overlapping windows, up to max_clips
- if no window meets min_seconds, fall back to the longest window and flag
  `relaxed_min_seconds` in the plan (never silently produces nothing)

Extraction cuts from final.mp4 with re-encode to h264/yuv420p + aac. It keeps
the source aspect ratio (landscape) — vertical reframe, caption re-render and
any Shorts upload are out of scope by design.
"""
import json
import os
import re
from datetime import datetime, timezone

from .media import run_ffmpeg, run_ffprobe
from .providers_tts import chunk_text
from .script_stage import parse_script
from .state import GateError, PipelineError, load, require_script_approval, sha256_file

DEFAULT_MAX_SECONDS = 60
DEFAULT_MIN_SECONDS = 15
DEFAULT_MAX_CLIPS = 3

_HOOK_RE = re.compile(
    r"^(why|how|what|when|who|where|which|did|does|do|is|are|was|were|can|could)\b",
    re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_CAP_ENTITY_RE = re.compile(r"^[A-Z][a-zA-Z'-]*$")


def _load_json(path, what):
    if not os.path.isfile(path):
        raise PipelineError(f"{what} missing: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _chunk_windows(vdir):
    """Chunks with cumulative start/end from durations.json."""
    durations = _load_json(os.path.join(vdir, "durations.json"),
                           "durations.json (run voice stage first)")
    windows, cursor = [], 0.0
    for i, entry in enumerate(durations):
        duration = float(entry.get("duration") or 0)
        windows.append({
            "id": f"chunk_{i + 1:03d}",
            "file": entry.get("file", ""),
            "text": (entry.get("text") or "").strip(),
            "start": cursor,
            "end": cursor + duration,
            "duration": duration,
        })
        cursor += duration
    if not windows:
        raise PipelineError("no audio chunks to plan from; run voice stage first")
    return windows, cursor


def _script_text(vdir):
    path = os.path.join(vdir, "script.md")
    if not os.path.isfile(path):
        raise PipelineError(f"script.md missing for shorts planning: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _claim_chunk_map(claims, windows):
    """Align claim texts to chunk windows in order.

    durations.json chunks are produced as claim-major, chunk_text()-split, so
    re-deriving the same split reproduces the mapping. A mismatch means the
    script changed after voice; refuse rather than guess.
    """
    mapped, idx = [], 0
    for claim in claims:
        texts = chunk_text(claim.get("text", ""))
        ids = []
        for _ in texts:
            if idx >= len(windows):
                raise PipelineError(
                    "durations.json has fewer chunks than script.md requires "
                    "after re-splitting; re-run voice stage")
            ids.append(windows[idx]["id"])
            idx += 1
        if ids:
            mapped.append((claim, ids))
    if idx != len(windows):
        raise PipelineError(
            f"durations.json has {len(windows)} chunks but script.md re-splits "
            f"to {idx}; re-run voice stage")
    return mapped


def _entity_count(text):
    """Rough named-entity count: capitalized tokens that are not sentence-initial."""
    words = _WORD_RE.findall(text or "")
    if not words:
        return 0
    count = 0
    for i, word in enumerate(words):
        if i == 0:
            continue
        if _CAP_ENTITY_RE.match(word):
            count += 1
    return count


def _chunk_score(chunk, is_first):
    text = chunk["text"]
    score = 0.0
    reasons = {}
    if is_first and _HOOK_RE.match(text):
        score += 2.0
        reasons["hook"] = 2.0
    entities = min(_entity_count(text), 6)
    if entities:
        score += entities * 0.5
        reasons["entities"] = round(entities * 0.5, 2)
    if re.search(r"\d", text):
        score += 0.5
        reasons["digits"] = 0.5
    questions = min(text.count("?"), 2)
    if questions:
        score += questions * 1.0
        reasons["question"] = float(questions)
    return score, reasons


def _candidate_score(chunks, span):
    score = 0.0
    best_hook = None
    for i, chunk in enumerate(chunks):
        chunk_score, _ = _chunk_score(chunk, i == 0)
        score += chunk_score
        if i == 0 and _HOOK_RE.match(chunk["text"]):
            best_hook = chunk["text"]
    if 25.0 <= span <= 55.0:
        score += 1.0
    return round(score, 4), best_hook


def _trim_to_word_boundary(text, limit):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    text = text.rstrip(".?!")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:")


def _title_suggestions(title, first_chunk_text):
    bare = _trim_to_word_boundary(first_chunk_text, 70) or "Untitled"
    series = _trim_to_word_boundary(title, 40) or "History"
    prefixed = _trim_to_word_boundary(f"{series}: {bare}", 100)
    suffix = _trim_to_word_boundary(f"{bare} — {series}", 100)
    return [bare, prefixed, suffix]


def _slugify(text, limit=50):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit].rstrip("-") or "short"


def _candidates(windows, max_seconds):
    for i in range(len(windows)):
        span = 0.0
        for j in range(i, len(windows)):
            span += windows[j]["duration"]
            if span > max_seconds:
                break
            yield i, j, span


def _build_plan(video_id, title, windows, total, cfg_shorts, max_clips):
    min_seconds = float(cfg_shorts.get("min_seconds", DEFAULT_MIN_SECONDS))
    max_seconds = float(cfg_shorts.get("max_seconds", DEFAULT_MAX_SECONDS))
    scored = []
    for i, j, span in _candidates(windows, max_seconds):
        chunks = windows[i:j + 1]
        score, hook = _candidate_score(chunks, span)
        scored.append({
            "i": i, "j": j, "span": span, "score": score, "hook": hook,
            "chunks": [c["id"] for c in chunks],
        })
    relaxed = False
    eligible = [c for c in scored if c["span"] >= min_seconds]
    if not eligible and scored:
        relaxed = True
        longest = max(c["span"] for c in scored)
        eligible = [c for c in scored if c["span"] == longest]
    eligible.sort(key=lambda c: (-c["score"], c["i"], c["span"]))
    picked, used = [], []
    for cand in eligible:
        if len(picked) >= max_clips:
            break
        if any(not (cand["j"] < a or cand["i"] > b) for a, b in used):
            continue
        picked.append(cand)
        used.append((cand["i"], cand["j"]))
    picked.sort(key=lambda c: c["i"])
    clips = []
    for n, cand in enumerate(picked, 1):
        first = windows[cand["i"]]
        bare = _trim_to_word_boundary(first["text"], 70) or "short"
        clips.append({
            "index": n,
            "start": round(first["start"], 3),
            "end": round(windows[cand["j"]]["end"], 3),
            "duration": round(cand["span"], 3),
            "score": cand["score"],
            "hook": cand["hook"],
            "chunks": cand["chunks"],
            "slug": _slugify(bare),
            "title_suggestions": _title_suggestions(title, first["text"]),
        })
    return clips, relaxed, len(scored)


def plan_shorts(runs_dir, video_id, cfg=None, max_clips=None):
    """Plan up to max_clips <=60s Shorts windows; write shorts/shorts_plan.json.

    Requires script approval (gates on script.md hash) and durations.json.
    """
    state = load(runs_dir, video_id)
    require_script_approval(state, runs_dir)
    vdir = os.path.join(runs_dir, video_id)
    title = state.get("title", "")
    windows, total = _chunk_windows(vdir)
    # Fail loud if the script changed after voice (chunk mapping mismatch).
    _, claims, _ = parse_script(_script_text(vdir))
    if claims:
        _claim_chunk_map(claims, windows)
    cfg = cfg or {}
    shorts_cfg = cfg.get("shorts") or {}
    clips_cfg = shorts_cfg.get("max_clips", DEFAULT_MAX_CLIPS)
    clips_n = int(max_clips if max_clips is not None else clips_cfg)
    if clips_n < 1:
        raise PipelineError(f"max_clips must be >= 1 (got {clips_n})")
    clips, relaxed, candidate_count = _build_plan(video_id, title, windows, total,
                                                  shorts_cfg, clips_n)
    out_dir = os.path.join(vdir, "shorts")
    os.makedirs(out_dir, exist_ok=True)
    plan = {
        "video_id": video_id,
        "title": title,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "final.mp4",
        "params": {
            "max_clips": clips_n,
            "min_seconds": float(shorts_cfg.get("min_seconds", DEFAULT_MIN_SECONDS)),
            "max_seconds": float(shorts_cfg.get("max_seconds", DEFAULT_MAX_SECONDS)),
        },
        "relaxed_min_seconds": relaxed,
        "total_duration": round(total, 3),
        "candidate_count": candidate_count,
        "clips": clips,
        "title_suggestions_note": (
            "Title suggestions are deterministic scaffolding; review before publishing."
        ),
    }
    plan_path = os.path.join(out_dir, "shorts_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    return plan


def _require_final_pinned(runs_dir, video_id):
    state = load(runs_dir, video_id)
    final = os.path.join(runs_dir, video_id, "final.mp4")
    if not os.path.isfile(final):
        raise PipelineError(f"final.mp4 missing for {video_id}; run assemble first")
    if not state.get("render_approved"):
        raise GateError(
            f"render not approved for {video_id}; run: approve render <id>")
    pinned = state.get("final_sha256_approved")
    if pinned:
        current = sha256_file(final)
        if current != pinned:
            raise GateError(
                f"final.mp4 changed after render approval for {video_id}; "
                "re-run: approve render <id>")
    return final


def extract_shorts(runs_dir, video_id, cfg=None):
    """Cut planned clips from final.mp4 (landscape) into shorts/short_NN.mp4.

    Requires render approval + final.mp4 hash pin (same contract as upload).
    """
    vdir = os.path.join(runs_dir, video_id)
    final = _require_final_pinned(runs_dir, video_id)
    plan_path = os.path.join(vdir, "shorts", "shorts_plan.json")
    plan = _load_json(plan_path, "shorts plan (run: shorts-plan <id> first)")
    clips = plan.get("clips") or []
    if not clips:
        raise PipelineError(f"shorts plan has no clips for {video_id}; re-run shorts-plan")
    out_dir = os.path.join(vdir, "shorts")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for clip in clips:
        out = os.path.join(out_dir, f"short_{clip['index']:02d}.mp4")
        run_ffmpeg([
            "-ss", f"{clip['start']:.3f}", "-to", f"{clip['end']:.3f}",
            "-i", final,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", out,
        ])
        info = run_ffprobe(out)
        duration = float((info.get("format") or {}).get("duration") or 0)
        if duration <= 0:
            raise PipelineError(f"extracted short has no duration: {out}")
        written.append({"file": f"short_{clip['index']:02d}.mp4",
                        "duration": round(duration, 3),
                        "sha256": sha256_file(out)})
    manifest = {
        "video_id": video_id,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "source": "final.mp4",
        "aspect": "landscape (source)",
        "shorts": written,
    }
    manifest_path = os.path.join(out_dir, "shorts_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest

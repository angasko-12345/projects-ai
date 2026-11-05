"""Scene manifest: one scene per script claim, with style lock and timings.

The manifest is the contract between the script and any visual generator. It
links each claim to the audio chunks that speak it, so assemble can pick the
image for a segment instead of cycling blindly.
"""
import json
import os
from datetime import datetime, timezone

from .providers_tts import chunk_text
from .script_stage import count_words, parse_script
from .state import PipelineError, load, require_script_approval, set_stage

CAMERAS = ("slow_push", "pan_left", "pan_right", "tilt_up", "static")
LIGHTING = ("low_key", "overcast", "warm_interior", "harsh_noon")
WORDS_PER_SECOND = 2.6


def _load_json(path, what):
    if not os.path.isfile(path):
        raise PipelineError(f"{what} missing: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _claims(vdir, state):
    script_path = os.path.join(vdir, "script.md")
    if not os.path.isfile(script_path):
        raise PipelineError(f"script.md missing for {state['video_id']}")
    with open(script_path, encoding="utf-8") as f:
        _, claims, _ = parse_script(f.read())
    if not claims:
        meta_path = os.path.join(vdir, "script_meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                claims = (json.load(f).get("claims") or [])
    if not claims:
        raise PipelineError(f"no claims in script for {state['video_id']}")
    return claims


def _split_timings(claims, durations):
    """Map each claim to its audio chunk indices by replaying the voice split.

    Index-based on purpose: older scripts can repeat claim ids per section, and
    ids are not guaranteed unique in hand-edited files. If durations.json does
    not line up with the approved script, refuse rather than guess.
    """
    if not durations:
        return [], []
    groups, idx = [], 0
    annotated = []
    for claim in claims:
        chunk_ids = []
        for _ in chunk_text(claim.get("text", "")):
            if idx >= len(durations):
                raise PipelineError(
                    "durations.json has fewer chunks than script.md requires; "
                    "re-run voice stage")
            entry = dict(durations[idx])
            entry["scene_id"] = f"SC{len(groups) + 1:03d}"
            annotated.append(entry)
            chunk_ids.append(idx)
            idx += 1
        groups.append(chunk_ids)
    if idx != len(durations):
        raise PipelineError(
            f"durations.json has {len(durations)} chunks but script.md re-splits "
            f"to {idx}; re-run voice stage")
    return groups, annotated


def build_manifest(runs_dir, video_id, cfg):
    state = load(runs_dir, video_id)
    require_script_approval(state, runs_dir)
    vdir = os.path.join(runs_dir, video_id)
    claims = _claims(vdir, state)
    durations_path = os.path.join(vdir, "durations.json")
    durations = None
    if os.path.isfile(durations_path):
        with open(durations_path, encoding="utf-8") as f:
            durations = json.load(f)
    chunk_groups, annotated = _split_timings(claims, durations)
    scene_cfg = (cfg or {}).get("scene") or {}
    style_lock = scene_cfg.get("style_lock") or ""
    period = scene_cfg.get("period") or ""
    location = scene_cfg.get("location") or ""
    scenes = []
    previous_section = None
    for index, claim in enumerate(claims, 1):
        section = claim.get("section") or ""
        if durations:
            seconds = sum(float(durations[i].get("duration") or 0)
                          for i in chunk_groups[index - 1])
        else:
            seconds = max(1.5, round(count_words(claim.get("text", "")) / WORDS_PER_SECOND * 2) / 2)
        transition = "dissolve" if previous_section not in (None, section) else "cut"
        previous_section = section
        scenes.append({
            "scene_id": f"SC{index:03d}",
            "claim_id": claim["id"],
            "section": section,
            "narration": claim.get("text", ""),
            "period": period,
            "location": location,
            "camera": CAMERAS[(index - 1) % len(CAMERAS)],
            "lighting": LIGHTING[(index - 1) % len(LIGHTING)],
            "transition": transition,
            "duration": round(seconds, 3),
            "continuity_ref": f"style:{style_lock}" if style_lock else "",
            "style_lock": style_lock,
            "asset": None,
        })
    manifest = {
        "video_id": video_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "style_lock": style_lock,
        "total_duration": round(sum(s["duration"] for s in scenes), 3),
        "scenes": scenes,
        "note": "period and location come from config; empty means not set, not guessed",
    }
    with open(os.path.join(vdir, "scene_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    if annotated:
        with open(durations_path, "w", encoding="utf-8") as f:
            json.dump(annotated, f, indent=2, ensure_ascii=False)
    set_stage(runs_dir, video_id, "scenes")
    return manifest


def load_manifest(runs_dir, video_id):
    path = os.path.join(runs_dir, video_id, "scene_manifest.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(runs_dir, video_id, manifest):
    path = os.path.join(runs_dir, video_id, "scene_manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return path


def scenes_stage(runs_dir, video_id, cfg):
    """Pipeline entry point; requires an approved script."""
    return build_manifest(runs_dir, video_id, cfg)

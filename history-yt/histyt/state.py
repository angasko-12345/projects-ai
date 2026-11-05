"""Per-video state in runs/<id>/state.json, plus approval gates."""
import hashlib
import json
import os
from datetime import datetime, timezone

STAGES = [
    "topic", "research", "awaiting_script_approval", "approved_script",
    "voice", "visuals", "assemble", "thumbnail", "variation_check",
    "awaiting_render_approval", "uploaded",
]

# Stages reachable only after script approval.
POST_SCRIPT_GATE = STAGES[STAGES.index("approved_script"):]


class GateError(Exception):
    pass


class PipelineError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def state_path(runs_dir, video_id):
    return os.path.join(runs_dir, video_id, "state.json")


def create(runs_dir, video_id, title, angle):
    os.makedirs(os.path.join(runs_dir, video_id), exist_ok=True)
    state = {
        "video_id": video_id,
        "title": title,
        "angle": angle or "",
        "stage": "topic",
        "template": "",
        "script_hash_approved": None,
        "render_approved": False,
        "final_sha256_approved": None,
        "thumbnail_phash": None,
        "thumbnail_layout": None,
        "thumbnail_seed": None,
        "completed_stages": [],
        "duration_warning": None,
        "created_at": _now(),
        "updated_at": _now(),
        "error": None,
    }
    save(runs_dir, video_id, state)
    return state


def save(runs_dir, video_id, state):
    os.makedirs(os.path.join(runs_dir, video_id), exist_ok=True)
    state["updated_at"] = _now()
    # Keep legacy state files working: fill in completed_stages if missing.
    state.setdefault("completed_stages", [])
    with open(state_path(runs_dir, video_id), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load(runs_dir, video_id):
    path = state_path(runs_dir, video_id)
    if not os.path.isfile(path):
        raise PipelineError(f"no state for video {video_id} (expected {path})")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_stage(runs_dir, video_id, stage):
    state = load(runs_dir, video_id)
    state["stage"] = stage
    save(runs_dir, video_id, state)
    return state


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def require_script_approval(state, runs_dir):
    """Raise GateError unless script.md exists with an approved hash that still matches."""
    stage = state.get("stage", "")
    approved = state.get("script_hash_approved")
    script = os.path.join(runs_dir, state["video_id"], "script.md")
    if not approved:
        raise GateError(
            f"script not approved for {state['video_id']} (stage={stage}); "
            "run: approve script <id>"
        )
    if not os.path.isfile(script):
        raise GateError(f"script.md missing for {state['video_id']}")
    current = sha256_file(script)
    if current != approved:
        raise GateError(
            f"script.md changed after approval for {state['video_id']}; "
            "re-run: approve script <id>"
        )
    if stage in ("topic", "research", "awaiting_script_approval"):
        raise GateError(f"stage {stage} is before the script-approval gate")
    return True

"""Stage runner with script/render approval gates."""
import os

from . import state as state_mod
from .assemble import assemble_stage
from .research import load_sources, research_stage
from .script_stage import generate_script
from .state import PipelineError, require_script_approval, sha256_file
from .thumbnail import make_thumbnail
from .upload import upload_stage
from .variation import variation_check_stage
from .visuals import visuals_stage
from .voice_stage import voice_stage

STAGE_ORDER = ["topic", "research", "script", "awaiting_script_approval",
               "approved_script", "voice", "visuals", "assemble", "thumbnail",
               "variation_check", "awaiting_render_approval", "awaiting_upload",
               "uploaded"]


def approve_script(runs_dir, video_id):
    script = os.path.join(runs_dir, video_id, "script.md")
    if not os.path.isfile(script):
        raise PipelineError(f"script.md missing for {video_id}")
    state = state_mod.load(runs_dir, video_id)
    state["script_hash_approved"] = sha256_file(script)
    state["stage"] = "approved_script"
    state_mod.save(runs_dir, video_id, state)
    return state


def approve_render(runs_dir, video_id):
    state = state_mod.load(runs_dir, video_id)
    state["render_approved"] = True
    state["stage"] = "awaiting_upload"
    state_mod.save(runs_dir, video_id, state)
    return state


def _has_sources(runs_dir, video_id):
    return len(load_sources(runs_dir, video_id)) >= 2


def run_until_gate(runs_dir, video_id, cfg, uploader=None):
    """Run pipeline, stopping at awaiting_script_approval / awaiting_render_approval."""
    state = state_mod.load(runs_dir, video_id)
    stage = state.get("stage", "topic")
    if stage in STAGE_ORDER[:2]:  # topic, research
        if stage == "topic":
            if not _has_sources(runs_dir, video_id):
                raise PipelineError(
                    f"no sources for {video_id}; add with: "
                    f"research {video_id} --url URL --title ...")
            research_stage(runs_dir, video_id, load_sources(runs_dir, video_id))
        # script
        generate_script(runs_dir, video_id, cfg)
        return state_mod.load(runs_dir, video_id)  # awaiting_script_approval
    if stage in ("awaiting_script_approval", "approved_script"):
        # Post-script gate: requires approval before continuing.
        require_script_approval(state, runs_dir)
        voice_stage(runs_dir, video_id, cfg)
        visuals_stage(runs_dir, video_id, cfg, state.get("title", ""))
        assemble_stage(runs_dir, video_id, cfg)
        make_thumbnail(runs_dir, video_id)
        variation_check_stage(runs_dir, video_id, cfg)
        state = state_mod.load(runs_dir, video_id)
        if stage != "variation_check" or not state.get("render_approved"):
            state["stage"] = "awaiting_render_approval"
            state_mod.save(runs_dir, video_id, state)
        if state.get("render_approved"):
            upload_stage(runs_dir, video_id, cfg, client=uploader)
        return state_mod.load(runs_dir, video_id)
    if stage in ("variation_check", "awaiting_render_approval", "awaiting_upload", "uploaded"):
        if not state.get("render_approved"):
            return state  # hydrate then stop at render gate
        if stage != "uploaded":
            upload_stage(runs_dir, video_id, cfg, client=uploader)
        return state_mod.load(runs_dir, video_id)
    raise PipelineError(f"unknown stage {stage!r} for {video_id}")


def status(runs_dir, video_id=None):
    if video_id:
        return state_mod.load(runs_dir, video_id)
    if not os.path.isdir(runs_dir):
        return []
    rows = []
    for vid in sorted(os.listdir(runs_dir)):
        if os.path.isfile(state_mod.state_path(runs_dir, vid)):
            rows.append(state_mod.load(runs_dir, vid))
    return rows

"""Stage runner with script/render approval gates."""
import os

from . import qa as qa_mod
from . import state as state_mod
from .assemble import assemble_stage
from .research import load_sources, research_stage
from .scenes import scenes_stage
from .script_stage import generate_script
from .state import (STAGE_ORDER, PipelineError, require_script_approval,
                    sha256_file)
from .thumbnail import make_thumbnail
from .upload import upload_stage
from .variation import variation_check_stage
from .visuals import visuals_stage
from .voice_stage import voice_stage

POST_SCRIPT_STAGES = ("awaiting_script_approval", "approved_script", "voice", "scenes",
                      "visuals", "assemble", "thumbnail", "variation_check", "qa")


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
    final = os.path.join(runs_dir, video_id, "final.mp4")
    if not os.path.isfile(final):
        raise PipelineError(f"final.mp4 missing for {video_id}; cannot approve render")
    report = qa_mod.load_report(runs_dir, video_id)
    if report is None:
        raise state_mod.GateError(
            f"QA report missing for {video_id}; run: qa {video_id}")
    if report.get("verdict") == "reject":
        reasons = ", ".join(report.get("auto_rejections") or []) or "see qa_report.json"
        raise state_mod.GateError(
            f"QA rejected {video_id}: {reasons}; fix and re-run: qa {video_id}")
    state["render_approved"] = True
    state["final_sha256_approved"] = state_mod.sha256_file(final)
    state["stage"] = "awaiting_upload"
    state_mod.save(runs_dir, video_id, state)
    return state


def _has_sources(runs_dir, video_id):
    return len(load_sources(runs_dir, video_id)) >= 2


def run_until_gate(runs_dir, video_id, cfg, uploader=None):
    """Run pipeline, stopping at awaiting_script_approval / awaiting_render_approval.

    Idempotent/resumable: each per-stage function runs only if its stage is not
    already in state["completed_stages"]. Failures record state["error"] and
    keep the last-good stage.
    """
    state = state_mod.load(runs_dir, video_id)
    completed = state.setdefault("completed_stages", [])

    def _mark_done(stage, label=None):
        state["error"] = None
        if stage not in completed:
            completed.append(stage)
        state["completed_stages"] = completed
        # Update the stage label as we go so state.json stays resumable.
        state["stage"] = label or stage
        state_mod.save(runs_dir, video_id, state)

    def _run(fn, stage_name, *a, stage_label=None, **kw):
        if stage_name in completed:
            return None  # already done; skip re-render
        try:
            result = fn(*a, **kw)
        except Exception as exc:
            st = state_mod.load(runs_dir, video_id)
            st["error"] = f"{type(exc).__name__}: {exc}"
            state_mod.save(runs_dir, video_id, st)
            raise
        _mark_done(stage_name, stage_label)
        return result

    stage = state.get("stage", "topic")

    if stage in STAGE_ORDER[:2]:  # topic, research
        if stage == "topic":
            if not _has_sources(runs_dir, video_id):
                raise PipelineError(
                    f"no sources for {video_id}; add with: "
                    f"research {video_id} --url URL --title ...")
            _run(research_stage, "research", runs_dir, video_id,
                 load_sources(runs_dir, video_id))
        _run(generate_script, "script", runs_dir, video_id, cfg,
             stage_label="awaiting_script_approval")
        return state_mod.load(runs_dir, video_id)  # awaiting_script_approval

    if stage in POST_SCRIPT_STAGES:
        # Post-script gate: requires approval before continuing.
        require_script_approval(state, runs_dir)
        _run(voice_stage, "voice", runs_dir, video_id, cfg)
        _run(scenes_stage, "scenes", runs_dir, video_id, cfg)
        _run(visuals_stage, "visuals", runs_dir, video_id, cfg,
             state.get("title", ""))
        _run(assemble_stage, "assemble", runs_dir, video_id, cfg)
        _run(make_thumbnail, "thumbnail", runs_dir, video_id)
        _run(variation_check_stage, "variation_check", runs_dir, video_id, cfg)
        _run(qa_mod.qa_stage, "qa", runs_dir, video_id, cfg)
        state = state_mod.load(runs_dir, video_id)
        if not state.get("render_approved"):
            state["stage"] = "awaiting_render_approval"
            state_mod.save(runs_dir, video_id, state)
        else:
            _run(upload_stage, "uploaded", runs_dir, video_id, cfg, client=uploader)
        return state_mod.load(runs_dir, video_id)

    if stage in ("awaiting_render_approval", "awaiting_upload", "uploaded"):
        if not state.get("render_approved"):
            return state  # hydrate then stop at render gate
        if stage != "uploaded" and "uploaded" not in completed:
            _run(upload_stage, "uploaded", runs_dir, video_id, cfg, client=uploader)
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

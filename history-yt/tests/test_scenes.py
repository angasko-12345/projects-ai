import json
import os

import pytest

from histyt import scenes
from histyt.script_stage import generate_script
from histyt.state import GateError
from histyt.visuals import visuals_stage
from histyt.voice_stage import voice_stage


def _prepared(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    from histyt import pipeline
    pipeline.approve_script(runs, vid)
    voice_stage(runs, vid, cfg)
    return vid


def test_scenes_require_script_approval(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    with pytest.raises(GateError):
        scenes.build_manifest(runs, vid, cfg)


def test_manifest_has_one_scene_per_claim_and_real_timings(runs, cfg, make_video):
    vid = _prepared(runs, cfg, make_video)
    manifest = scenes.build_manifest(runs, vid, cfg)
    vdir = os.path.join(runs, vid)
    with open(os.path.join(vdir, "script_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    with open(os.path.join(vdir, "durations.json"), encoding="utf-8") as f:
        durations = json.load(f)
    assert len(manifest["scenes"]) == len(meta["claims"])
    assert abs(manifest["total_duration"]
               - sum(float(d["duration"]) for d in durations)) < 0.01
    assert all(entry.get("scene_id") for entry in durations)
    assert manifest["style_lock"]
    assert manifest["scenes"][0]["continuity_ref"].startswith("style:")
    sections = {scene["section"] for scene in manifest["scenes"]}
    if len(sections) > 1:
        assert any(scene["transition"] == "dissolve" for scene in manifest["scenes"])
    assert manifest["scenes"][0]["transition"] == "cut"


def test_visuals_assign_scene_assets_and_record_style_lock(runs, cfg, make_video):
    vid = _prepared(runs, cfg, make_video)
    scenes.build_manifest(runs, vid, cfg)
    visuals_stage(runs, vid, cfg)
    manifest = scenes.load_manifest(runs, vid)
    assert all(scene["asset"] for scene in manifest["scenes"])
    assert all(os.path.isfile(scene["asset"]) for scene in manifest["scenes"])
    with open(os.path.join(runs, vid, "assets.json"), encoding="utf-8") as f:
        assets = json.load(f)
    assert len(assets) == len(manifest["scenes"])
    assert all(asset.get("style_lock") for asset in assets)
    assert len({asset["path"] for asset in assets}) == len(assets)

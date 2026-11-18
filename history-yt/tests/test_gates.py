import os

import pytest

from histyt import pipeline, state
from histyt.script_stage import generate_script
from histyt.state import GateError


def test_edit_after_approval_blocks_voice(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    script = os.path.join(runs, vid, "script.md")
    with open(script, "a", encoding="utf-8") as f:
        f.write("\nEdited after approval\n")
    st = state.load(runs, vid)
    with pytest.raises(GateError):
        state.require_script_approval(st, runs)


def test_upload_requires_render_approval(runs, cfg, make_video, tmp_path):
    vid = make_video()
    from histyt.upload import MockYouTubeClient, upload_stage
    with pytest.raises(GateError):
        upload_stage(runs, vid, cfg, client=MockYouTubeClient())

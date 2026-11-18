import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from histyt import topics
from histyt.research import research_stage


@pytest.fixture()
def cfg():
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "config", "channel.yaml"), encoding="utf-8") as f:
        c = yaml.safe_load(f)
    c["llm"] = {"provider": "fake"}
    c["tts"] = {"provider": "fake", "pacing_min": 0.9, "pacing_max": 1.1}
    # Keep fake renders fast, and do not apply release thresholds to silence.
    c["target_length_minutes"] = 1
    c.setdefault("qa", {})["profile"] = "structural"
    return c


@pytest.fixture()
def runs(tmp_path):
    return str(tmp_path / "runs")


@pytest.fixture()
def make_video(runs, cfg):
    """make_video(title, with_sources=True) -> video_id ready for script stage."""
    def _make(title="The Roman Empire", angle="origins", with_sources=True):
        vid = topics.add_topic(runs, title, angle)
        if with_sources:
            research_stage(runs, vid, [
                {"id": "S1", "url": "https://example.com/a", "title": "A", "note": ""},
                {"id": "S2", "url": "https://example.com/b", "title": "B", "note": ""},
            ])
        return vid
    return _make

"""Export specs the first real render failed: frame-exact segments, real loudness.

Both checks exist because QA rejected the first real ElevenLabs render: the
concat averaged 29.907 fps instead of 30, and loudness landed at -15.3 LUFS
with a -0.7 dBTP peak against a -14/-1 spec.
"""
import json
import os
import shutil

import pytest

from histyt import pipeline
from histyt.assemble import _measure_loudnorm, _normalize_loudness, assemble_stage
from histyt.media import get_streams, run_ffmpeg
from histyt.providers_tts import chunk_text
from histyt.script_stage import parse_script
from histyt.scenes import scenes_stage
from histyt.visuals import visuals_stage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

SENTENCES = [
    ("Why did the Roman army vanish in a German forest?", 0.6),
    ("Three legions marched in and none came back.", 0.6),
    ("The frontier never recovered its old line.", 0.6),
]


def _video_with_durations(runs, cfg, make_video, sentences):
    vid = make_video()
    vdir = os.path.join(runs, vid)
    script = "\n".join(f"[C{i + 1}] {text} (S1, S2)"
                       for i, (text, _) in enumerate(sentences))
    with open(os.path.join(vdir, "script.md"), "w", encoding="utf-8") as f:
        f.write("# Specs\n\n" + script + "\n")
    pipeline.approve_script(runs, vid)
    _, claims, _ = parse_script("# Specs\n\n" + script + "\n")
    durations, idx = [], 0
    for claim, (_, seconds) in zip(claims, sentences, strict=True):
        for chunk in chunk_text(claim["text"]):
            idx += 1
            durations.append({"file": f"audio/chunk_{idx:03d}.wav", "text": chunk,
                              "duration": float(seconds), "pace": 1.0})
    os.makedirs(os.path.join(vdir, "audio"), exist_ok=True)
    for entry in durations:
        # Silence is fine here: this test is about timing, not loudness.
        run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", f"{entry['duration']}",
                    os.path.join(vdir, entry["file"])])
    with open(os.path.join(vdir, "durations.json"), "w", encoding="utf-8") as f:
        json.dump(durations, f, indent=2, ensure_ascii=False)
    return vid, durations


def _fps(path):
    stream = next(s for s in get_streams(path) if s.get("codec_type") == "video")
    num, den = (stream.get("avg_frame_rate") or "0/1").split("/")
    return float(num) / float(den) if float(den) else 0.0


def test_concatenated_render_keeps_the_configured_frame_rate(runs, cfg, make_video):
    vid, durations = _video_with_durations(runs, cfg, make_video, SENTENCES)
    spec_cfg = dict(cfg)
    spec_cfg["videos"] = {"width": 640, "height": 360, "fps": 30}
    spec_cfg["audio"] = {"normalize": False}
    scenes_stage(runs, vid, spec_cfg)
    visuals_stage(runs, vid, spec_cfg)
    info = assemble_stage(runs, vid, spec_cfg)
    assert _fps(info["final"]) == pytest.approx(30.0, abs=0.01)
    narration = sum(d["duration"] for d in durations)
    # Each concat join can carry a frame of padding; drift must not accumulate
    # past a frame per join, which is what pushed 28 chunks to 29.907 fps.
    assert info["duration"] == pytest.approx(narration, abs=0.04 * len(durations))


def test_loudness_normalization_hits_target_and_peak_limit(tmp_path):
    # Bursts at full scale over a quiet bed: low integrated level with near-0
    # peaks, the case where one-pass loudnorm ends up quiet or over the limit.
    source = str(tmp_path / "source.mp4")
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=320x180:r=30",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
                "-af", "volume='if(lt(mod(t,2),0.1),1,0.16)':eval=frame",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", source])
    cfg = {"audio": {"normalize": True, "loudness_lufs": -14.0},
           "qa": {"true_peak_max": -1.0}}
    assert _normalize_loudness(source, cfg) is True
    data = _measure_loudnorm(source, -14.0, -1.0, 11.0)
    # The contract QA enforces: inside its loudness tolerance and under the
    # true-peak ceiling. The pre-fix render failed both (-15.3 LUFS, -0.7 dBTP).
    assert float(data["input_i"]) == pytest.approx(-14.0, abs=1.0)
    assert float(data["input_tp"]) <= -1.0

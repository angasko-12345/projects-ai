"""Voice stage: synthesize one chunk per claim sentence (deterministic pace)."""
import hashlib
import json
import os

from . import state as state_mod
from .providers_tts import chunk_text, make_tts_provider
from .script_stage import parse_script
from .state import require_script_approval, set_stage


def _pace_for(chunk, pacing_min, pacing_max):
    """Deterministic speed multiplier in [pacing_min, pacing_max].

    Providers apply it (Piper as length_scale, ElevenLabs as speed); the fake
    provider ignores it by design.
    """
    h = int(hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:8], 16)
    frac = (h % 100) / 100.0
    return pacing_min + frac * (pacing_max - pacing_min)


def voice_stage(runs_dir, video_id, cfg, provider=None):
    state = state_mod.load(runs_dir, video_id)
    require_script_approval(state, runs_dir)  # enforces script-approval gate internally
    vdir = os.path.join(runs_dir, video_id)
    with open(os.path.join(vdir, "script.md"), encoding="utf-8") as f:
        script_text = f.read()
    _, claims, _ = parse_script(script_text)
    if not claims:
        # Fall back to script_meta so section headers don't break parsing.
        with open(os.path.join(vdir, "script_meta.json"), encoding="utf-8") as f:
            claims = (json.load(f).get("claims") or [])
    tts_cfg = cfg.get("tts") or {}
    pacing_min = float(tts_cfg.get("pacing_min", 0.9))
    pacing_max = float(tts_cfg.get("pacing_max", 1.1))
    provider = provider or make_tts_provider(cfg)
    audio_dir = os.path.join(vdir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    durations = []
    idx = 0
    for claim in claims:
        for chunk in chunk_text(claim.get("text", "")):
            idx += 1
            pace = _pace_for(chunk, pacing_min, pacing_max)
            out_wav = os.path.join(audio_dir, f"chunk_{idx:03d}.wav")
            duration = provider.synthesize_chunk(chunk, out_wav, pace)
            durations.append({"file": f"audio/chunk_{idx:03d}.wav", "text": chunk,
                              "duration": duration, "pace": pace})
    with open(os.path.join(vdir, "durations.json"), "w", encoding="utf-8") as f:
        json.dump(durations, f, indent=2, ensure_ascii=False)
    set_stage(runs_dir, video_id, "voice")
    return durations

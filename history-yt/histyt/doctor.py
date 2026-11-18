"""Environment + config health checks. Never prints secret values."""
import os
import requests
import shutil
import subprocess
import sys

from .config import ConfigError, ensure_d_confined


def _check(name, ok, detail):
    return (name, bool(ok), detail)


def _ffmpeg_version(ffmpeg_path):
    try:
        proc = subprocess.run([ffmpeg_path, "-version"], capture_output=True,
                              timeout=10)
        first = proc.stdout.decode("utf-8", "replace").splitlines() or [""]
        return first[0].strip()[:200]
    except (OSError, subprocess.SubprocessError):
        return "unknown version"


def _check_api_key(name, env_var, url, key):
    """Cheap models-list usability probe (10s timeout); never logs the key."""
    if not key:
        return _check(name, True, "absent (optional)")
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if resp.status_code == 200:
            return _check(name, True, "usable (HTTP 200)")
        return _check(name, False, f"HTTP {resp.status_code}")
    except (requests.RequestException, ValueError) as exc:
        return _check(name, False, str(exc)[:200])


def doctor(cfg, runs_dir):
    checks = []
    checks.append(_check("python", sys.version_info >= (3, 10), sys.version.split()[0]))
    checks.append(_check("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "not on PATH"))
    checks.append(_check("ffprobe", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "not on PATH"))
    for mod, label in (("PIL", "pillow"), ("yaml", "pyyaml"), ("requests", "requests")):
        try:
            __import__(mod)
            checks.append(_check(label, True, "installed"))
        except ImportError:
            checks.append(_check(label, False, "not installed"))
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "channel.yaml")
    checks.append(_check("config", os.path.isfile(config_path),
                         config_path if os.path.isfile(config_path) else "missing"))
    try:
        os.makedirs(runs_dir, exist_ok=True)
        probe = os.path.join(runs_dir, ".writable")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        checks.append(_check("runs writable", True, runs_dir))
    except OSError as exc:
        checks.append(_check("runs writable", False, str(exc)))
    try:
        ensure_d_confined(runs_dir)
        checks.append(_check("runs on D:", True, runs_dir))
    except ConfigError as exc:
        checks.append(_check("runs on D:", False, str(exc)))
    tts = (cfg or {}).get("tts") or {}
    provider = (tts.get("provider") or "fake").lower()
    if provider == "piper":
        exe = tts.get("piper_exe") or "piper"
        model = tts.get("model") or ""
        checks.append(_check("piper exe", bool(shutil.which(exe) or os.path.isfile(exe)), exe))
        on_d = model and model.upper().startswith("D:")
        checks.append(_check("piper model", bool(model and os.path.isfile(model)),
                             f"{model or '(unset)'} [on D:: {bool(on_d)}]"))
    else:
        checks.append(_check("tts provider", True, provider or "fake"))
    eleven_key = os.environ.get("ELEVENLABS_API_KEY", "")
    checks.append(_check("ELEVENLABS_API_KEY", bool(eleven_key) or provider != "elevenlabs",
                         "present" if eleven_key else "absent"))
    if provider == "elevenlabs" and not eleven_key:
        checks.append(_check("elevenlabs usable", False,
                             "tts.provider is elevenlabs but ELEVENLABS_API_KEY is absent"))
    checks.append(_check("OPENROUTER_API_KEY", True,
                         "present" if os.environ.get("OPENROUTER_API_KEY") else "absent"))
    checks.append(_check("GOOGLE_API_KEY", True,
                         "present" if os.environ.get("GOOGLE_API_KEY") else "absent"))
    # Optional usability probes (cheap models-list, 10s timeout, never key).
    checks.append(_check_api_key(
        "openrouter usable", "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1/models", os.environ.get("OPENROUTER_API_KEY", "")))
    checks.append(_check_api_key(
        "google usable", "GOOGLE_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/models", os.environ.get("GOOGLE_API_KEY", "")))
    # ffmpeg Gyan build detection (Priority 2, item 6).
    ffmpeg_path = shutil.which("ffmpeg") or ""
    if ffmpeg_path:
        ver = _ffmpeg_version(ffmpeg_path)
        gyan = "gyan" in ver.lower()
        checks.append(_check("ffmpeg build", True, ver or ffmpeg_path + (" [Gyan build]" if gyan else "")))
    else:
        checks.append(_check("ffmpeg build", False, "not on PATH"))
    youtube = (cfg or {}).get("youtube") or {}
    client_json = os.environ.get("HISTYT_YT_CLIENT_JSON") or youtube.get("oauth_client_json") or ""
    token = os.environ.get("HISTYT_YT_TOKEN") or youtube.get("token_path") or ""
    checks.append(_check("youtube client json", bool(client_json and os.path.isfile(client_json)),
                         client_json or "set env HISTYT_YT_CLIENT_JSON"))
    checks.append(_check("youtube token", bool(token and os.path.isfile(token)),
                         token or "set env HISTYT_YT_TOKEN"))
    return checks


def format_doctor(checks):
    lines = []
    for name, ok, detail in checks:
        lines.append(f"{'OK  ' if ok else 'FAIL'} {name}: {detail}")
    return "\n".join(lines)

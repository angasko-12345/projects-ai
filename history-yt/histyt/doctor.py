"""Environment + config health checks. Never prints secret values."""
import os
import shutil
import sys


def _check(name, ok, detail):
    return (name, bool(ok), detail)


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
    tts = (cfg or {}).get("tts") or {}
    provider = (tts.get("provider") or "").lower()
    if provider == "piper":
        exe = tts.get("piper_exe") or "piper"
        model = tts.get("model") or ""
        checks.append(_check("piper exe", bool(shutil.which(exe) or os.path.isfile(exe)), exe))
        checks.append(_check("piper model", bool(model and os.path.isfile(model)), model or "(unset)"))
    else:
        checks.append(_check("tts provider", True, f"{provider or 'fake'} (no piper needed)"))
    checks.append(_check("OPENROUTER_API_KEY", True,
                         "present" if os.environ.get("OPENROUTER_API_KEY") else "absent"))
    checks.append(_check("GOOGLE_API_KEY", True,
                         "present" if os.environ.get("GOOGLE_API_KEY") else "absent"))
    youtube = (cfg or {}).get("youtube") or {}
    client_json = os.environ.get("HISTYT_YT_CLIENT_JSON") or youtube.get("oauth_client_json") or ""
    token = os.environ.get("HISTYT_YT_TOKEN") or youtube.get("token_path") or ""
    checks.append(_check("youtube client json", bool(client_json and os.path.isfile(client_json)),
                         client_json or "(unset)"))
    checks.append(_check("youtube token", bool(token and os.path.isfile(token)),
                         token or "(unset)"))
    return checks


def format_doctor(checks):
    lines = []
    for name, ok, detail in checks:
        lines.append(f"{'OK  ' if ok else 'FAIL'} {name}: {detail}")
    return "\n".join(lines)

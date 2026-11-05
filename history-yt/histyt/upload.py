"""YouTube upload (private only). No google-api deps at runtime; plain requests."""
import json
import os

import requests

from . import state as state_mod
from .assemble import build_description
from .config import get
from .research import load_sources
from .state import GateError, PipelineError

YOUTUBE_DOCS_URL = "https://developers.google.com/youtube/v3/docs/videos/insert"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status"


class YouTubeClient:
    """Minimal surface: insert(video_path, thumb_path, body) -> videoId."""

    def insert(self, video_path, thumb_path, body):
        raise NotImplementedError


class MockYouTubeClient(YouTubeClient):
    def __init__(self, video_id="mock-123"):
        self.video_id = video_id
        self.calls = []

    def insert(self, video_path, thumb_path, body):
        assert body.get("status", {}).get("privacyStatus") == "private", \
            "privacyStatus must always be private"
        self.calls.append({"video_path": video_path, "thumb_path": thumb_path, "body": body})
        return self.video_id


class RealYouTubeClient(YouTubeClient):
    def __init__(self, token_path, timeout=120):
        if not token_path or not os.path.isfile(token_path):
            raise PipelineError(
                f"YouTube token not found at {token_path!r}; set youtube.token_path "
                "(env HISTYT_YT_TOKEN)")
        with open(token_path, encoding="utf-8") as f:
            creds = json.load(f)
        self.token = creds.get("access_token") or ""
        if not self.token:
            raise PipelineError("YouTube token file has no access_token field")
        self.timeout = timeout

    def insert(self, video_path, thumb_path, body):
        metadata = json.dumps(body).encode("utf-8")
        with open(video_path, "rb") as f:
            media = f.read()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "multipart/related; boundary=histyt_boundary",
        }
        payload = (
            b"--histyt_boundary\r\n"
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + metadata + b"\r\n"
            b"--histyt_boundary\r\n"
            b"Content-Type: video/mp4\r\n\r\n" + media + b"\r\n"
            b"--histyt_boundary--\r\n"
        )
        try:
            resp = requests.post(UPLOAD_URL, data=payload, headers=headers,
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            raise PipelineError(f"YouTube upload request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise PipelineError(f"YouTube upload HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            return resp.json()["id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError(f"unexpected YouTube response: {exc}") from exc


def check_synthetic_field(timeout=15):
    """Check the official docs for containsSyntheticMedia; False on failure (no crash)."""
    try:
        resp = requests.get(YOUTUBE_DOCS_URL, timeout=timeout)
        resp.raise_for_status()
        return "containsSyntheticMedia" in resp.text
    except Exception:
        return False


def build_body(cfg, title, description):
    """status.privacyStatus is ALWAYS the literal 'private'. Synthetic flag is
    config-driven only (set when the user verified the docs); never guessed."""
    youtube_cfg = cfg.get("youtube") or {}
    status = {"privacyStatus": "private"}
    synthetic = get(youtube_cfg, "contains_synthetic_media", None)
    if synthetic is not None:
        status["containsSyntheticMedia"] = bool(synthetic)
    body = {
        "snippet": {"title": title, "description": description},
        "status": status,
    }
    return body


def upload_stage(runs_dir, video_id, cfg, client=None):
    state = state_mod.load(runs_dir, video_id)
    if not state.get("render_approved"):
        raise GateError(f"render not approved for {video_id}; run: approve render <id>")
    vdir = os.path.join(runs_dir, video_id)
    final = os.path.join(vdir, "final.mp4")
    thumb = os.path.join(vdir, "thumbnail.png")
    for path in (final, thumb):
        if not os.path.isfile(path):
            raise PipelineError(f"required file missing: {path}")
    youtube_cfg = cfg.get("youtube") or {}
    if get(youtube_cfg, "contains_synthetic_media", None) is None:
        print("ACTION REQUIRED: set synthetic/AI disclosure toggle manually in "
              "YouTube Studio — field not verified in this build")
    meta_path = os.path.join(vdir, "script_meta.json")
    script_meta = {}
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            script_meta = json.load(f)
    description = build_description(script_meta, load_sources(runs_dir, video_id),
                                    state.get("title", ""))
    body = build_body(cfg, state.get("title", ""), description)
    if body["status"]["privacyStatus"] != "private":
        raise PipelineError("privacyStatus must be private; refusing non-private upload")
    client = client or RealYouTubeClient(_token_path(cfg))
    returned_id = client.insert(final, thumb, body)
    record = {"video_id": returned_id, "privacyStatus": "private",
              "title": state.get("title", "")}
    with open(os.path.join(vdir, "upload.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    state_mod.set_stage(runs_dir, video_id, "uploaded")
    return record


def _token_path(cfg):
    youtube_cfg = cfg.get("youtube") or {}
    return (os.environ.get("HISTYT_YT_TOKEN")
            or youtube_cfg.get("token_path") or "")

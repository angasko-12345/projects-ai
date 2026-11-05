"""Visual assets: license gate, local title/timeline cards, fetch helper."""
import json
import os

import requests
from PIL import Image, ImageDraw, ImageFont

from .state import PipelineError, set_stage

ALLOWED_LICENSES = {"public domain", "cc0", "us federal work", "cc-by"}
GENERATED_LICENSE = "public domain"  # generated locally; no third-party asset

CARD_W, CARD_H = 1920, 1080


def _normalize(license_text):
    return (license_text or "").strip().lower()


def validate_asset(asset):
    if _normalize(asset.get("license")) not in ALLOWED_LICENSES:
        raise PipelineError(
            f"license not allowed: {asset.get('license')!r} "
            f"(allowed: {sorted(ALLOWED_LICENSES)})"
        )
    return True


def write_assets(runs_dir, video_id, assets):
    for asset in assets:
        validate_asset(asset)
    vdir = os.path.join(runs_dir, video_id)
    os.makedirs(vdir, exist_ok=True)
    rows = [{
        "path": a.get("path", ""),
        "license": a.get("license", ""),
        "source_url": a.get("source_url", ""),
        "attribution": a.get("attribution", ""),
    } for a in assets]
    with open(os.path.join(vdir, "assets.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    return rows


def _gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)
    return img


def _wrap(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:4]


def _try_font(size):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def generate_cards(runs_dir, video_id, title, cfg=None):
    """Create title.png + timeline.png locally; no external asset needed."""
    vdir = os.path.join(runs_dir, video_id)
    img_dir = os.path.join(vdir, "images")
    os.makedirs(img_dir, exist_ok=True)
    videos = (cfg or {}).get("videos") or {}
    w = int(videos.get("width", CARD_W))
    h = int(videos.get("height", CARD_H))

    img = _gradient(w, h, (20, 20, 40), (60, 40, 20))
    draw = ImageDraw.Draw(img)
    font = _try_font(64)
    for i, line in enumerate(_wrap(draw, title or "Untitled", font, int(w * 0.8))):
        draw.text((int(w * 0.1), int(h * 0.4) + i * 80), line, font=font, fill=(255, 255, 255))
    title_path = os.path.join(img_dir, "title.png")
    img.save(title_path)

    timeline = _gradient(w, h, (40, 20, 20), (20, 40, 60))
    tdraw = ImageDraw.Draw(timeline)
    tfont = _try_font(48)
    tdraw.text((int(w * 0.1), int(h * 0.2)), "Timeline", font=tfont, fill=(255, 255, 255))
    years = ["1000", "1200", "1400", "1600", "1800", "2000"]
    for i, year in enumerate(years):
        x = int(w * 0.1 + i * (w * 0.8 / max(len(years) - 1, 1)))
        tdraw.ellipse([x - 8, int(h * 0.6) - 8, x + 8, int(h * 0.6) + 8], fill=(255, 200, 100))
        tdraw.text((x - 30, int(h * 0.6) + 30), year, font=_try_font(36), fill=(255, 255, 255))
    timeline_path = os.path.join(img_dir, "timeline.png")
    timeline.save(timeline_path)

    assets = [
        {"path": title_path, "license": GENERATED_LICENSE,
         "source_url": "generated locally", "attribution": "histyt"},
        {"path": timeline_path, "license": GENERATED_LICENSE,
         "source_url": "generated locally", "attribution": "histyt"},
    ]
    validate = write_assets(runs_dir, video_id, assets)
    return validate


def fetch_asset(url, dest, license, attribution, source_url, timeout=30):
    """Validate license FIRST, then download. Raises PipelineError on bad license."""
    validate_asset({"license": license})
    if not url.startswith("https://"):
        raise PipelineError(f"only https URLs allowed: {url}")
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "histyt/0.1.0"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise PipelineError(f"asset fetch failed for {url}: {exc}") from exc
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(resp.content)
    return dest


class DisabledAIImageProvider:
    """AI image generation is disabled by default (config ai_image.provider)."""

    def generate(self, *args, **kwargs):
        raise PipelineError("AI image generation disabled by default")


def visuals_stage(runs_dir, video_id, cfg, title=None):
    from . import state as state_mod
    state = state_mod.load(runs_dir, video_id)
    vdir = os.path.join(runs_dir, video_id)
    assets_path = os.path.join(vdir, "assets.json")
    if os.path.isfile(assets_path):
        with open(assets_path, encoding="utf-8") as f:
            assets = json.load(f)
        for asset in assets:
            validate_asset(asset)
    else:
        assets = generate_cards(runs_dir, video_id, title or state.get("title", ""), cfg)
    set_stage(runs_dir, video_id, "visuals")
    return assets

"""Visual assets: license gate, local scene cards, fetch helper.

Cards are a placeholder renderer, not the final visual system. When a scene
manifest exists, one card is generated per scene with a deterministic palette
derived from the style lock, and every asset records which scene it serves.
"""
import hashlib
import json
import os

import requests
from PIL import Image, ImageDraw, ImageFont

from . import scenes as scenes_mod
from . import state as state_mod
from .state import PipelineError, set_stage

ALLOWED_LICENSES = {"public domain", "cc0", "us federal work", "cc-by"}
GENERATED_LICENSE = "public domain"  # generated locally; no third-party asset

CARD_W, CARD_H = 1920, 1080
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "DejaVuSans-Bold.ttf",
)
PALETTES = (
    ((22, 24, 32), (58, 47, 38)),
    ((18, 30, 36), (48, 56, 40)),
    ((34, 24, 24), (62, 42, 30)),
    ((24, 28, 24), (52, 58, 46)),
    ((26, 26, 38), (46, 40, 58)),
    ((32, 26, 20), (68, 52, 34)),
)


def _normalize(license_text):
    return (license_text or "").strip().lower()


def validate_asset(asset):
    if _normalize(asset.get("license")) not in ALLOWED_LICENSES:
        raise PipelineError(
            f"license not allowed: {asset.get('license')!r} "
            f"(allowed: {sorted(ALLOWED_LICENSES)})"
        )
    return True


def image_signature(path):
    """64-bit average hash; catches the same image reused across scenes.

    An average hash is enough for duplicate detection and needs no numpy. It is
    not a backstop for near-duplicates of a different crop; the QA slice will
    compare a wider Hamming distance only if that turns out to matter.
    """
    with Image.open(path) as img:
        small = img.convert("L").resize((8, 8))
        flattened = getattr(small, "get_flattened_data", None)
        pixels = list(flattened() if flattened else small.getdata())
    if not pixels:
        return ""
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel > average else "0" for pixel in pixels)
    return f"{int(bits, 2):016x}"


def hash_distance(first, second):
    """Hamming distance between two signatures: 0 identical, 64 opposite."""
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def write_assets(runs_dir, video_id, assets):
    for asset in assets:
        validate_asset(asset)
    vdir = os.path.join(runs_dir, video_id)
    os.makedirs(vdir, exist_ok=True)
    rows = []
    for asset in assets:
        row = {
            "path": asset.get("path", ""),
            "license": asset.get("license", ""),
            "source_url": asset.get("source_url", ""),
            "attribution": asset.get("attribution", ""),
        }
        for key in ("scene_id", "style_lock", "tier", "license_url", "license_name",
                    "visual_type", "provider", "model", "prompt", "seed", "width",
                    "height", "phash", "query", "credit_line", "sha256"):
            if asset.get(key) is not None and asset.get(key) != "":
                row[key] = asset[key]
        rows.append(row)
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


def _wrap(draw, text, font, max_width, max_lines=4):
    words = (text or "").split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines


def _try_font(size):
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _palette_for(style_lock, scene_id):
    digest = hashlib.sha256(f"{style_lock}|{scene_id}".encode()).hexdigest()
    return PALETTES[int(digest[:8], 16) % len(PALETTES)]


def _draw_card(path, w, h, top, bottom, label, body, footer):
    img = _gradient(w, h, top, bottom)
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(w * 0.08), int(h * 0.16), int(w * 0.084), int(h * 0.84)],
                   fill=(200, 168, 112))
    label_font = _try_font(max(20, int(h * 0.028)))
    body_font = _try_font(max(30, int(h * 0.062)))
    footer_font = _try_font(max(18, int(h * 0.024)))
    if label:
        draw.text((int(w * 0.12), int(h * 0.18)), label.upper(), font=label_font,
                  fill=(224, 200, 158))
    lines = _wrap(draw, body, body_font, int(w * 0.76), max_lines=3)
    base_y = int(h * 0.38)
    for index, line in enumerate(lines):
        draw.text((int(w * 0.12), base_y + index * int(h * 0.09)), line,
                  font=body_font, fill=(245, 242, 236))
    if footer:
        draw.text((int(w * 0.12), int(h * 0.84)), footer, font=footer_font,
                  fill=(190, 184, 170))
    img.save(path)


def generate_cards(runs_dir, video_id, title, cfg=None):
    """Fallback: title and timeline cards, no scene manifest needed."""
    vdir = os.path.join(runs_dir, video_id)
    img_dir = os.path.join(vdir, "images")
    os.makedirs(img_dir, exist_ok=True)
    videos = (cfg or {}).get("videos") or {}
    w = int(videos.get("width", CARD_W))
    h = int(videos.get("height", CARD_H))
    title_path = os.path.join(img_dir, "title.png")
    timeline_path = os.path.join(img_dir, "timeline.png")
    _draw_card(title_path, w, h, (20, 20, 40), (60, 40, 20),
               "history", title or "Untitled", "histyt")
    _draw_card(timeline_path, w, h, (40, 20, 20), (20, 40, 60),
               "timeline", "The long arc of events", "histyt")
    assets = [
        {"path": title_path, "license": GENERATED_LICENSE,
         "source_url": "generated locally", "attribution": "histyt"},
        {"path": timeline_path, "license": GENERATED_LICENSE,
         "source_url": "generated locally", "attribution": "histyt"},
    ]
    return write_assets(runs_dir, video_id, assets)


def generate_scene_cards(runs_dir, video_id, manifest, cfg=None):
    """One deterministic card per scene, derived from the style lock."""
    vdir = os.path.join(runs_dir, video_id)
    img_dir = os.path.join(vdir, "images", "scenes")
    os.makedirs(img_dir, exist_ok=True)
    videos = (cfg or {}).get("videos") or {}
    w = int(videos.get("width", CARD_W))
    h = int(videos.get("height", CARD_H))
    style_lock = (manifest or {}).get("style_lock") or ""
    assets = []
    for scene in (manifest or {}).get("scenes") or []:
        scene_id = scene.get("scene_id") or "SC"
        path = os.path.join(img_dir, f"{scene_id}.png")
        top, bottom = _palette_for(style_lock, scene_id)
        excerpt = scene.get("narration") or scene.get("section") or ""
        words = " ".join(excerpt.split()[:12])
        _draw_card(path, w, h, top, bottom, scene.get("section") or "history",
                   words, f"{scene_id}  |  {scene.get('section', '')}".strip(" |"))
        assets.append({
            "path": path,
            "license": GENERATED_LICENSE,
            "source_url": "generated locally",
            "attribution": "histyt",
            "scene_id": scene_id,
            "style_lock": style_lock,
        })
    if not assets:
        raise PipelineError("scene manifest has no scenes")
    return write_assets(runs_dir, video_id, assets)


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


def _existing_assets(runs_dir, video_id):
    path = os.path.join(runs_dir, video_id, "assets.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        assets = json.load(f)
    for asset in assets:
        validate_asset(asset)
    return assets


def _scene_asset_path(vdir, path):
    return path if os.path.isabs(path) else os.path.join(vdir, path)


def visuals_stage(runs_dir, video_id, cfg, title=None):
    state = state_mod.load(runs_dir, video_id)
    manifest = scenes_mod.load_manifest(runs_dir, video_id)
    assets = _existing_assets(runs_dir, video_id)
    if manifest and assets is not None:
        vdir = os.path.join(runs_dir, video_id)
        scenes_list = manifest.get("scenes") or []
        pinned = [scene for scene in scenes_list
                  if scene.get("asset")
                  and os.path.isfile(_scene_asset_path(vdir, scene["asset"]))]
        if scenes_list and len(pinned) == len(scenes_list):
            # Archive assets are already chosen per scene; cards would replace
            # them with placeholders, so this stage is a no-op.
            set_stage(runs_dir, video_id, "visuals")
            return assets
    if assets is None:
        if manifest:
            assets = generate_scene_cards(runs_dir, video_id, manifest, cfg)
        else:
            assets = generate_cards(runs_dir, video_id, title or state.get("title", ""), cfg)
    if manifest:
        paths = [asset.get("path", "") for asset in assets if asset.get("path")]
        if not paths:
            raise PipelineError("assets.json has no image paths for the scene manifest")
        for index, scene in enumerate(manifest.get("scenes") or []):
            scene["asset"] = paths[index % len(paths)]
        scenes_mod.save_manifest(runs_dir, video_id, manifest)
    set_stage(runs_dir, video_id, "visuals")
    return assets

"""Seeded 1280x720 thumbnails + dhash for variation checks."""
import hashlib
import itertools
import json
import os

from PIL import Image, ImageDraw, ImageEnhance

from . import state as state_mod
from .state import set_stage
from .visuals import _gradient, _try_font, _wrap

THUMB_W, THUMB_H = 1280, 720


def _seed_int(video_id, seed=None):
    if seed is not None:
        return int(seed)
    return int(hashlib.sha256((video_id or "").encode()).hexdigest()[:8], 16)


def _layout_bold_left(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(72)
    draw.rectangle([0, 0, int(THUMB_W * 0.55), THUMB_H], fill=(0, 0, 0, 120))
    for i, line in enumerate(_wrap(draw, text, font, int(THUMB_W * 0.5))):
        draw.text((40, 60 + i * 90), line, font=font, fill=(255, 255, 255))


def _layout_centered(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(72)
    lines = _wrap(draw, text, font, int(THUMB_W * 0.8))
    total = len(lines) * 90
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        draw.text(((THUMB_W - w) / 2, (THUMB_H - total) / 2 + i * 90), line,
                  font=font, fill=(255, 255, 255),
                  stroke_width=4, stroke_fill=(0, 0, 0))


def _layout_split(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(64)
    draw.rectangle([THUMB_W // 2, 0, THUMB_W, THUMB_H], fill=(0, 0, 0, 110))
    for i, line in enumerate(_wrap(draw, text, font, int(THUMB_W * 0.42))):
        draw.text((THUMB_W // 2 + 40, 80 + i * 84), line, font=font, fill=(255, 255, 255))


def _layout_bottom_band(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(56)
    band_h = 200
    draw.rectangle([0, THUMB_H - band_h, THUMB_W, THUMB_H], fill=(0, 0, 0, 140))
    for i, line in enumerate(_wrap(draw, text, font, int(THUMB_W * 0.9))):
        draw.text((40, THUMB_H - band_h + 20 + i * 70), line, font=font, fill=(255, 255, 255))


def _layout_top_banner(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(60)
    draw.rectangle([0, 0, THUMB_W, 180], fill=(0, 0, 0, 120))
    for i, line in enumerate(_wrap(draw, text, font, int(THUMB_W * 0.85))):
        draw.text((40, 30 + i * 75), line, font=font, fill=(255, 255, 255))


def _layout_duotone_panel(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(66)
    draw.rectangle([int(THUMB_W * 0.52), 0, THUMB_W, THUMB_H], fill=(180, 60, 20, 160))
    for i, line in enumerate(_wrap(draw, text, font, int(THUMB_W * 0.4))):
        draw.text((int(THUMB_W * 0.54), 90 + i * 88), line, font=font, fill=(255, 255, 255))


def _layout_letterbox(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(68)
    draw.rectangle([0, THUMB_H - 240, THUMB_W, THUMB_H], fill=(10, 10, 30, 170))
    for i, line in enumerate(_wrap(draw, text, font, int(THUMB_W * 0.85))):
        draw.text((40, THUMB_H - 210 + i * 85), line, font=font, fill=(255, 255, 255))


def _layout_compact_caption(img, text):
    draw = ImageDraw.Draw(img, "RGBA")
    font = _try_font(54)
    lines = _wrap(draw, text, font, int(THUMB_W * 0.6))
    box_h = max(120, len(lines) * 70 + 30)
    draw.rectangle([30, THUMB_H - box_h - 20, int(THUMB_W * 0.72), THUMB_H - 20],
                   fill=(0, 0, 0, 170))
    for i, line in enumerate(lines):
        draw.text((50, THUMB_H - box_h - 10 + i * 70), line, font=font,
                  fill=(255, 255, 255))


THUMB_LAYOUTS = [_layout_bold_left, _layout_centered, _layout_split, _layout_bottom_band,
                 _layout_top_banner, _layout_duotone_panel, _layout_letterbox,
                 _layout_compact_caption]
LAYOUT_NAMES = ["bold-left", "centered", "split", "bottom-band",
                "top-banner", "duotone-panel", "letterbox", "compact-caption"]


def dhash_image(path):
    """8x8 grayscale differences -> 64-bit int rendered as hex string."""
    with Image.open(path) as img:
        small = img.convert("L").resize((8, 8))
        px = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
    value = 0
    for i, (a, b) in enumerate(itertools.pairwise(px)):
        if i % 8 == 7:
            continue
        if a > b:
            value |= 1 << (63 - i)
    return f"{value:016x}"


def hamming(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _base_image(vdir, seed):
    assets_path = os.path.join(vdir, "assets.json")
    if os.path.isfile(assets_path):
        with open(assets_path, encoding="utf-8") as f:
            for asset in json.load(f):
                p = asset.get("path", "")
                if p.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    ap = p if os.path.isabs(p) else os.path.join(vdir, p)
                    if os.path.isfile(ap):
                        return Image.open(ap).convert("RGB")
    return _gradient(THUMB_W, THUMB_H, (20, 20, 40), (70, 30, 10))


def make_thumbnail(runs_dir, video_id, seed=None, layout=None):
    state = state_mod.load(runs_dir, video_id)
    vdir = os.path.join(runs_dir, video_id)
    seed_int = _seed_int(video_id, seed)
    layout_idx = layout if isinstance(layout, int) else seed_int % len(THUMB_LAYOUTS)
    layout_fn = THUMB_LAYOUTS[layout_idx % len(THUMB_LAYOUTS)]
    base = _base_image(vdir, seed_int).resize((THUMB_W, THUMB_H))
    # Seeded color tint for visual variation.
    base = ImageEnhance.Color(base).enhance(1.0 + (seed_int % 30) / 100.0)
    tint = Image.new("RGB", (THUMB_W, THUMB_H),
                     ((seed_int >> 16) & 0xFF, (seed_int >> 8) & 0xFF, seed_int & 0xFF))
    tinted = Image.blend(base, tint, 0.12)
    layout_fn(tinted.convert("RGBA") if False else tinted, state.get("title", ""))
    out = os.path.join(vdir, "thumbnail.png")
    tinted.save(out)
    phash = dhash_image(out)
    meta = {"layout": LAYOUT_NAMES[layout_idx % len(LAYOUT_NAMES)], "seed": seed_int, "phash": phash}
    with open(os.path.join(vdir, "thumbnail_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    state["thumbnail_phash"] = phash
    state["thumbnail_layout"] = meta["layout"]
    state["thumbnail_seed"] = seed_int
    state_mod.save(runs_dir, video_id, state)
    set_stage(runs_dir, video_id, "thumbnail")
    return meta

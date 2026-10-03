"""
Procedural scene planning and rendering.

Replaces the Pexels footage stage entirely. Scenes are described declaratively
(``ScenePlan``) and drawn by FFmpeg from pure math: animated gradients, a
blended fractal or cellular-automaton layer, drifting particles, a moving crop
window for Ken Burns-style camera work, and the scene's own keywords as text.

No stock-media API, no image assets, no network access.
"""
import os
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .config import get_ffmpeg_path
from .renderer import _run

WIDTH = 1080
HEIGHT = 1920
FPS = 30

# Gradients are generated oversized so the Ken Burns crop window always has
# pixels to travel into; 1.25x leaves room in every direction.
OVERSCAN = 1.25
BASE_W = int(WIDTH * OVERSCAN)   # 1350
BASE_H = int(HEIGHT * OVERSCAN)  # 2400

# Fractals and automata are expensive per pixel and end up heavily blurred, so
# they render small and get upscaled.
OVERLAY_W = 540
OVERLAY_H = 960


class VisualError(Exception):
    pass


# Curated palettes keep the output looking deliberate rather than random noise.
# Each entry is (dark, mid, accent).
PALETTES = (
    ("0x0d1b3e", "0x6a11cb", "0x00d4ff"),   # abyss
    ("0x2b0a18", "0xb3123c", "0xffb703"),   # ember
    ("0x041f1e", "0x0f766e", "0x5eead4"),   # lagoon
    ("0x1a1033", "0x5b21b6", "0xf472b6"),   # nebula
    ("0x0b1220", "0x1e3a8a", "0x38bdf8"),   # deep sea
    ("0x23140a", "0x78350f", "0xfcd34d"),   # desert
)

STYLES = ("nebula", "cells", "aurora", "shards")
MOTIONS = ("push_in", "pull_out", "pan_left", "pan_right", "drift")
TRANSITIONS = ("fade", "smoothleft", "smoothup", "circleopen", "radial",
               "wipeleft", "dissolve")

# Transition length, and the floor for a single scene so short clips stay sane.
TRANSITION_SECONDS = 0.6
MIN_SCENE_SECONDS = 2.0

# Legibility scrim over the top of every scene, where the keywords sit.
# Bright palettes wash white text out, so the ramp is stacked bands with
# falling alpha, then blurred into a smooth gradient.
TOP_SCRIM_START = 0.0
BAND_HEIGHT = 0.055
BANDS = 9
TOP_SCRIM_ALPHA = 0.55

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def find_font() -> Optional[str]:
    """Return a usable bold font file path, or None when none exists."""
    env = os.getenv("VISUAL_FONT")
    if env and Path(env).is_file():
        return env
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


@dataclass
class ScenePlan:
    """One scene's visual recipe, derived from the script text."""

    index: int
    text: str
    keywords: str
    duration: float
    style: str
    motion: str
    transition: str
    seed: int
    palette: Sequence[str]
    particles: List[dict] = field(default_factory=list)


def _style_for(rng: random.Random, index: int) -> str:
    """Pick a style, keeping the first scene distinct from the rest."""
    if index == 0:
        return "nebula"
    return rng.choice(STYLES)


def _make_particles(rng: random.Random, palette: Sequence[str]) -> List[dict]:
    """A handful of small drifting shapes for parallax and depth."""
    count = rng.randint(2, 4)
    out = []
    for _ in range(count):
        out.append({
            "size": rng.choice([8, 10, 12, 14, 18]),
            "color": rng.choice(list(palette)),
            "speed_x": rng.choice([-190, -140, -95, 95, 140, 190]),
            "speed_y": rng.choice([-70, -45, 45, 70]),
            "y0": rng.randint(120, HEIGHT - 260),
            "alpha": round(rng.uniform(0.25, 0.6), 2),
        })
    return out
def plan_scenes(script, duration: float = 45.0, seed: int = 0,
                num_scenes: Optional[int] = None) -> List[ScenePlan]:
    """Turn the script into an ordered list of scene recipes.

    Scene count scales with narration length so a long script gets more cuts,
    and each scene's duration is weighted by how much text it carries. The
    total is padded by the transition overlaps so the finished track lands
    exactly on ``duration``.
    """
    from . import script as sc

    sentences = sc.split_into_scenes(script, num_scenes or 999)
    if not sentences:
        return []

    if not num_scenes:
        # ~6s per scene, clamped to a sane editorial range.
        num_scenes = max(3, min(7, int(round(duration / 6.0)) or 3))
    # Never invent scenes beyond the sentences we actually have.
    num_scenes = max(1, min(num_scenes, len(sentences)))

    # Regroup sentences into contiguous chunks so narration flows in order
    # instead of being sampled out of sequence.
    chunks: List[str] = []
    total = len(sentences)
    for i in range(num_scenes):
        start = int(i * total / num_scenes)
        end = int((i + 1) * total / num_scenes)
        chunk = " ".join(sentences[start:end]).strip()
        if chunk:
            chunks.append(chunk)
    if not chunks:
        return []

    rng = random.Random(seed or 0)
    weights = [max(1, len(c.split())) for c in chunks]
    weight_sum = float(sum(weights))

    # Each xfade boundary consumes TRANSITION_SECONDS, so the scenes must add up
    # to duration + (n-1) * transition for the chain to end on ``duration``.
    n = len(chunks)
    budget = max(float(duration), MIN_SCENE_SECONDS * n) + (n - 1) * TRANSITION_SECONDS

    plans: List[ScenePlan] = []
    palette_offset = rng.randrange(len(PALETTES))

    for i, chunk in enumerate(chunks):
        share = budget * (weights[i] / weight_sum)
        palette = PALETTES[(palette_offset + i) % len(PALETTES)]
        plans.append(ScenePlan(
            index=i,
            text=chunk,
            keywords=sc.get_scene_keywords(chunk),
            duration=round(max(share, MIN_SCENE_SECONDS), 3),
            style=_style_for(rng, i),
            motion=rng.choice(MOTIONS),
            transition=rng.choice(TRANSITIONS),
            seed=rng.randrange(1, 2 ** 31 - 1),
            palette=palette,
            particles=_make_particles(rng, palette),
        ))

    return plans


def _escape_path(path: str) -> str:
    """Make a Windows path safe to embed in a filter argument."""
    return path.replace("\\", "/").replace(":", r"\:")


def _ken_burns(scene: ScenePlan, label_in: str, label_out: str) -> str:
    """Add camera movement to a scene.

    Zoom motions use ``zoompan`` because ``crop`` evaluates ``w``/``h`` once at
    graph-config time, where ``t`` is still undefined, so a ``t``-driven crop
    size fails with "Error when evaluating the expression". Pan motions only
    animate ``x``/``y``, which ``crop`` does evaluate per frame.
    """
    d = max(scene.duration, 0.1)
    total_x = BASE_W - WIDTH
    total_y = BASE_H - HEIGHT
    m = scene.motion
    # zoompan drives its output from `on`, the output frame number.
    frames = max(1, int(round(d * FPS)))

    if m == "push_in":
        return (f"[{label_in}]zoompan=z='1+0.22*on/{frames}':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
                f"setsar=1[{label_out}]")
    if m == "pull_out":
        return (f"[{label_in}]zoompan=z='1.22-0.22*on/{frames}':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
                f"setsar=1[{label_out}]")

    if m == "pan_left":
        cx = f"{total_x}-{total_x}*t/{d:.3f}"
        cy = f"{total_y / 2:.1f}"
    elif m == "pan_right":
        cx = f"{total_x}*t/{d:.3f}"
        cy = f"{total_y / 2:.1f}"
    else:  # drift: slow diagonal wander on a sine path
        cx = f"{total_x / 2:.1f}+{total_x * 0.45:.1f}*sin(t/2.4)"
        cy = f"{total_y / 2:.1f}+{total_y * 0.45:.1f}*cos(t/3.1)"

    # Clamp so the sine wander never walks the window off the canvas.
    expr = (f"crop=w={WIDTH}:h={HEIGHT}:"
            f"x='max(0\\,min(iw-ow\\,{cx}))':y='max(0\\,min(ih-oh\\,{cy}))'")
    return f"[{label_in}]{expr},setsar=1[{label_out}]"


def _overlay_chain(scene: ScenePlan, label: str) -> List[str]:
    """Build the overlay layers for a scene's chosen style."""
    accent = scene.palette[2]
    parts: List[str] = []

    if scene.style == "nebula":
        # Blurred fractal screened over the gradient.
        parts.append(
            f"mandelbrot=s={OVERLAY_W}x{OVERLAY_H}:r={FPS}:"
            f"start_x=-0.743643887:start_y=0.1318259:start_scale=3:end_scale=0.3:"
            f"morphxf=0.01:morphyf=0.0123[{label}_ovsrc]"
        )
        parts.append(
            f"[{label}_ovsrc]scale={BASE_W}:{BASE_H}:flags=bicubic,"
            f"gblur=sigma=7,format=yuva420p,colorchannelmixer=aa=0.55[{label}_ov]"
        )
        parts.append(
            f"[{label}_bg][{label}_ov]blend=all_mode=screen:all_opacity=0.42[{label}_bl]"
        )
        parts.append(
            f"[{label}_bl]hue=h=1.2*t:s=1.35,"
            # screen blending a fractal lifts the midtones hard; pull them back
            # so white text and the subtitle band stay legible.
            f"eq=brightness=-0.07:saturation=1.15[{label}_mix]"
        )
    elif scene.style == "cells":
        # Cellular automaton scrolling under the gradient. The raw automaton is
        # far too busy to sit behind text, so it is blurred hard and screened in
        # at low opacity; only the slow large-scale motion should survive.
        parts.append(
            f"cellauto=s={OVERLAY_W}x{OVERLAY_H}:r={FPS}:rule=110:scroll=1:"
            f"random_fill_ratio=0.4:seed={scene.seed}[{label}_ovsrc]"
        )
        parts.append(
            f"[{label}_ovsrc]scale={BASE_W}:{BASE_H}:flags=neighbor,"
            f"gblur=sigma=18,format=yuva420p,colorchannelmixer=aa=0.22[{label}_ov]"
        )
        parts.append(
            f"[{label}_bg][{label}_ov]blend=all_mode=softlight:all_opacity=0.9[{label}_bl]"
        )
        parts.append(f"[{label}_bl]hue=h=2*t:s=1.2,gblur=sigma=6,"
                       f"eq=brightness=-0.05[{label}_mix]")
    elif scene.style == "aurora":
        # No overlay: sweeping hue plus heavy blur reads as soft light bands.
        parts.append(f"[{label}_bg]hue=h=1.6*t:s=1.45,gblur=sigma=14[{label}_bl]")
        parts.append(f"[{label}_bl]vignette=PI/3,hue=h=40*t:s=1.1[{label}_mix]")
    else:  # shards: drifting geometric grid
        parts.append(
            f"[{label}_bg]hue=h=1.1*t:s=1.25,"
            f"drawgrid=w=190:h=190:t=2:c={accent}@0.16[{label}_bl]"
        )
        parts.append(f"[{label}_bl]vignette=PI/4,gblur=sigma=1.2[{label}_mix]")

    return parts


def _particle_chain(scene: ScenePlan, label_in: str, label_out: str) -> str:
    """Drifting squares that give the frame depth and movement."""
    filters = []
    for p in scene.particles:
        s = p["size"]
        # mod() wraps the shape around so it re-enters instead of vanishing.
        x = f"mod(t*{p['speed_x']}+{p['y0']}\\,{WIDTH + s})-{s}"
        y = f"mod({p['y0']}+t*{p['speed_y']}\\,{HEIGHT + s})-{s}"
        filters.append(
            f"drawbox=x='{x}':y='{y}':w={s}:h={s}:"
            f"color={p['color']}@{p['alpha']}:t=fill"
        )
    return f"[{label_in}]{','.join(filters)}[{label_out}]"


def _scrim_chain(scene: ScenePlan, label_in: str, label_out: str) -> str:
    """Add a soft dark ramp at the top so on-screen text stays readable.

    Bright palettes wash white text out completely. A single opaque ``drawbox``
    looks like a hard-edged bar, so the ramp is built from stacked bands with
    falling alpha and then blurred into a smooth gradient. One band per
    ``BAND`` units of height, each ``BAND_ALPHA`` weaker than the one above.
    """
    d = max(scene.duration, 0.1)
    bands = []
    for i in range(BANDS):
        # Alpha falls off linearly so the ramp has no visible steps.
        alpha = round(TOP_SCRIM_ALPHA * (1.0 - i / BANDS), 4)
        y = f"ih*{TOP_SCRIM_START:.3f}+ih*{BAND_HEIGHT:.4f}*{i}"
        h = f"ih*{BAND_HEIGHT:.4f}"
        bands.append(f"drawbox=x=0:y={y}:w=iw:h={h}:color=black@{alpha}:t=fill")
    # Build the ramp on a transparent canvas and blur only that layer, so the
    # scene underneath stays sharp.
    return (
        f"color=c=black@0.0:s={WIDTH}x{HEIGHT}:r={FPS}:d={d:.3f},"
        f"format=yuva420p,{','.join(bands)},gblur=sigma=12[{label_in}_scrimov];"
        f"[{label_in}][{label_in}_scrimov]overlay=0:0:format=auto[{label_out}]"
    )


def _text_chain(scene: ScenePlan, label_in: str, label_out: str,
                font: Optional[str], text_dir: Path) -> str:
    """Burn the scene's keywords and a scene counter into the frame.

    drawtext parses its own options out of the filter string, so scene text is
    passed via a file instead of inline. A BOM in that file makes FFmpeg render
    nothing at all, so it is written as plain UTF-8 without one, and referenced
    by bare filename (with cwd set) to dodge Windows drive-letter escaping.
    """
    keyword_file = text_dir / f"scene{scene.index}.txt"
    keyword_file.write_text((scene.keywords or "").strip(), encoding="utf-8")

    counter_file = text_dir / f"scene{scene.index}_n.txt"
    counter_file.write_text(f"SCENE {scene.index + 1}", encoding="utf-8")

    font_arg = _escape_path(font)
    big = 92 if len(scene.keywords or "") <= 18 else 66

    body = (
        f"drawtext=fontfile='{font_arg}':textfile={keyword_file.name}:"
        f"fontsize={big}:fontcolor=white@0.93:borderw=4:bordercolor=black@0.6:"
        f"line_spacing=12:x=(w-text_w)/2:y=h*0.115,"
        f"drawtext=fontfile='{font_arg}':textfile={counter_file.name}:"
        f"fontsize=34:fontcolor=white@0.45:borderw=2:bordercolor=black@0.4:"
        f"x=(w-text_w)/2:y=h*0.055"
    )
    return f"[{label_in}]{body}[{label_out}]"
def build_filtergraph(scenes: List[ScenePlan], text_dir: Path,
                      font: Optional[str]) -> str:
    """Compose the complete filter_complex for a scene sequence.

    Each scene becomes a gradient plus an overlay, blended, Ken-Burns cropped,
    decorated with particles and keywords, then chained with xfade so cuts land
    on the narration's scene boundaries.
    """
    parts: List[str] = []
    scene_labels: List[str] = []

    for scene in scenes:
        dark, mid, accent = scene.palette
        d = max(scene.duration, 0.1)
        label = f"s{scene.index}"

        parts.append(
            f"gradients=s={BASE_W}x{BASE_H}:r={FPS}:c0={dark}:c1={mid}:c2={accent}:"
            f"nb_colors=3:seed={scene.seed}:speed=0.04:d={d:.3f}[{label}_bg]"
        )
        parts.extend(_overlay_chain(scene, label))
        parts.append(_ken_burns(scene, f"{label}_mix", f"{label}_move"))
        parts.append(_scrim_chain(scene, f"{label}_move", f"{label}_scrim"))
        parts.append(_particle_chain(scene, f"{label}_scrim", f"{label}_parts"))
        if font:
            parts.append(_text_chain(scene, f"{label}_parts",
                                     f"{label}_txt", font, text_dir))
            label_in = f"{label}_txt"
        else:
            label_in = f"{label}_parts"
        # Vignette and grain unify every style so cut points do not jump.
        parts.append(
            f"[{label_in}]noise=alls=5:allf=t+u,vignette=PI/4,setsar=1,"
            f"format=yuv420p,fps={FPS}[{label}_out]"
        )
        scene_labels.append(f"{label}_out")

    if len(scene_labels) == 1:
        parts.append(f"[{scene_labels[0]}]null[vout]")
        return ";".join(parts)

    # Chain scenes with xfade. Scene lengths were padded by the transition
    # overlaps, so the running total minus consumed overlaps is the xfade offset.
    current = scene_labels[0]
    elapsed = scenes[0].duration
    for i in range(1, len(scenes)):
        offset = max(0.0, elapsed - TRANSITION_SECONDS * i)
        out = f"x{i}"
        parts.append(
            f"[{current}][{scene_labels[i]}]xfade=transition={scenes[i].transition}:"
            f"duration={TRANSITION_SECONDS:.3f}:offset={offset:.3f}[{out}]"
        )
        current = out
        elapsed += scenes[i].duration

    parts.append(f"[{current}]null[vout]")
    return ";".join(parts)


def render_background(scenes: List[ScenePlan], out_path: Path,
                      duration: float, work_dir: Optional[Path] = None
                      ) -> Path:
    """Render the silent animated visual track for a whole video."""
    if not scenes:
        raise VisualError("No scenes were planned for this video")

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    font = find_font()
    # FFmpeg runs with cwd set to the text directory so bare textfile names
    # resolve, which makes relative input/output paths ambiguous. Pin them first.
    work_dir = Path(work_dir).resolve() if work_dir else out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    text_dir = work_dir / "text"
    shutil.rmtree(text_dir, ignore_errors=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    filtergraph = build_filtergraph(scenes, text_dir, font)

    tmp_out = out_path.with_name(out_path.stem + ".partial.mp4")
    cmd = [
        get_ffmpeg_path(),
        "-y", "-hide_banner", "-loglevel", "error",
        "-filter_complex", filtergraph,
        "-map", "[vout]",
        "-t", f"{float(duration):.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(tmp_out),
    ]

    try:
        # cwd is the text directory so bare textfile names resolve, mirroring how
        # the renderer sidesteps Windows path escaping for subtitles.
        _run(cmd, cwd=text_dir, timeout=3600)
        if not tmp_out.is_file() or tmp_out.stat().st_size < 10_000:
            raise VisualError("FFmpeg produced no usable background track")
    except BaseException:
        if tmp_out.exists():
            try:
                tmp_out.unlink()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(text_dir, ignore_errors=True)

    tmp_out.replace(out_path)
    return out_path
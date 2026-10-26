"""
Campaign brief loading for ``clipcheck``.

A campaign brief is a small TOML file holding the rules a finished clip and
its post text must satisfy: hashtags and their order, required mentions, the
logo call-to-action's minimum on-screen time, the length window, loudness, and
so on. Every rule is optional: a rule that is not configured is reported as
``skip`` by the checker, never as a silent pass and never as a guess.

Errors name the file and the offending key and never surface as a traceback,
because the people writing these files are clippers, not Python developers.

Format (all sections and keys optional; see ``examples/campaign.example.toml``)::

    [campaign]
    name = "..."

    [video]
    length_range = [15.0, 28.0]   # seconds, inclusive
    max_static_seconds = 1.5      # longest allowed frozen run before the CTA

    [audio]
    loudness_target = [-15.0, -14.0]   # integrated LUFS, inclusive
    max_true_peak = -1.0               # dBTP

    [cta]
    logo_path = "logo.png"        # relative to this TOML file
    min_seconds = 2.0
    min_width_fraction = 0.25     # smaller matches (corner watermarks) don't count
    match_threshold = 0.70        # normalized cross-correlation, 0..1

    [post]
    required_hashtags = ["#brand", "#topic"]   # in this exact order
    hashtags_must_be_last = true
    required_mentions = ["BrandName"]          # case-insensitive
    disclosure_line = "AI voice"               # must appear if set
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 without tomllib
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None


class CampaignError(Exception):
    """A campaign file that cannot be used, with a message naming file and key."""


_HASHTAG_RE = re.compile(r"^#\w+$", re.UNICODE)

# section -> {key: kind}. Unknown keys are rejected so a typo ("min_secs")
# cannot silently disable a rule.
_SCHEMA: dict[str, dict[str, str]] = {
    "campaign": {"name": "str"},
    "video": {"length_range": "range", "max_static_seconds": "pos_float"},
    "audio": {"loudness_target": "range", "max_true_peak": "float"},
    "cta": {
        "logo_path": "str",
        "min_seconds": "pos_float",
        "min_width_fraction": "fraction",
        "match_threshold": "fraction",
    },
    "post": {
        "required_hashtags": "hashtags",
        "hashtags_must_be_last": "bool",
        "required_mentions": "str_list",
        "disclosure_line": "str",
    },
}

DEFAULT_CTA_MIN_WIDTH_FRACTION = 0.25
DEFAULT_CTA_MATCH_THRESHOLD = 0.70


@dataclass(frozen=True)
class Campaign:
    """A validated campaign brief. ``None`` means the rule is not configured."""

    source: Path
    name: str = ""
    length_range: tuple[float, float] | None = None
    max_static_seconds: float | None = None
    loudness_target: tuple[float, float] | None = None
    max_true_peak: float | None = None
    cta_logo_path: Path | None = None
    cta_min_seconds: float | None = None
    cta_logo_min_width_fraction: float = DEFAULT_CTA_MIN_WIDTH_FRACTION
    cta_logo_match_threshold: float = DEFAULT_CTA_MATCH_THRESHOLD
    required_hashtags: tuple[str, ...] = ()
    hashtags_must_be_last: bool = False
    required_mentions: tuple[str, ...] = ()
    disclosure_line: str | None = None


def _err(path: Path, where: str, problem: str) -> CampaignError:
    return CampaignError(f"Campaign file {path}: {where}: {problem}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _coerce(path: Path, where: str, kind: str, value):
    if kind == "str":
        if not isinstance(value, str):
            raise _err(path, where, f"expected text, got {type(value).__name__}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise _err(path, where, "expected true or false")
        return value
    if kind == "float":
        if not _is_number(value):
            raise _err(path, where, "expected a number")
        return float(value)
    if kind == "pos_float":
        if not _is_number(value) or float(value) <= 0:
            raise _err(path, where, "expected a number greater than 0")
        return float(value)
    if kind == "fraction":
        if not _is_number(value) or not 0 < float(value) <= 1:
            raise _err(path, where, "expected a number above 0 and at most 1")
        return float(value)
    if kind == "range":
        if (not isinstance(value, list) or len(value) != 2
                or not all(_is_number(v) for v in value)):
            raise _err(path, where, "expected a two-number list like [min, max]")
        lo, hi = float(value[0]), float(value[1])
        if lo > hi:
            raise _err(path, where, f"min {lo} is greater than max {hi}")
        return (lo, hi)
    if kind == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                                  for v in value):
            raise _err(path, where, "expected a list of non-empty text values")
        return tuple(v.strip() for v in value)
    if kind == "hashtags":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise _err(path, where, "expected a list of hashtags like [\"#brand\"]")
        tags = tuple(v.strip() for v in value)
        for tag in tags:
            if not _HASHTAG_RE.match(tag):
                raise _err(path, where,
                           f"{tag!r} is not a hashtag (must start with # and have no spaces)")
        lowered = [t.lower() for t in tags]
        if len(set(lowered)) != len(lowered):
            raise _err(path, where, "the same hashtag is listed twice")
        return tags
    raise AssertionError(f"unknown schema kind {kind}")  # pragma: no cover


def parse_campaign(data: dict, path: Path) -> Campaign:
    """Validate an already-decoded TOML mapping. Split out for unit tests."""
    path = Path(path)
    values: dict[str, object] = {}
    for section, body in data.items():
        if section not in _SCHEMA:
            raise _err(path, f"[{section}]",
                       f"unknown section (expected one of: {', '.join(_SCHEMA)})")
        if not isinstance(body, dict):
            raise _err(path, section, "expected a [section] table")
        allowed = _SCHEMA[section]
        for key, value in body.items():
            where = f"[{section}] {key}"
            if key not in allowed:
                raise _err(path, where,
                           f"unknown key (expected one of: {', '.join(allowed)})")
            values[f"{section}.{key}"] = _coerce(path, where, allowed[key], value)

    logo = values.get("cta.logo_path")
    logo_path = None
    if logo:
        logo_path = Path(str(logo)).expanduser()
        if not logo_path.is_absolute():
            logo_path = (path.parent / logo_path)
        logo_path = logo_path.resolve()
        if not logo_path.is_file():
            raise _err(path, "[cta] logo_path", f"file not found: {logo_path}")

    disclosure = values.get("post.disclosure_line")
    disclosure = str(disclosure).strip() if disclosure is not None else None

    return Campaign(
        source=path,
        name=str(values.get("campaign.name", "")),
        length_range=values.get("video.length_range"),
        max_static_seconds=values.get("video.max_static_seconds"),
        loudness_target=values.get("audio.loudness_target"),
        max_true_peak=values.get("audio.max_true_peak"),
        cta_logo_path=logo_path,
        cta_min_seconds=values.get("cta.min_seconds"),
        cta_logo_min_width_fraction=values.get(
            "cta.min_width_fraction", DEFAULT_CTA_MIN_WIDTH_FRACTION),
        cta_logo_match_threshold=values.get(
            "cta.match_threshold", DEFAULT_CTA_MATCH_THRESHOLD),
        required_hashtags=values.get("post.required_hashtags", ()),
        hashtags_must_be_last=bool(values.get("post.hashtags_must_be_last", False)),
        required_mentions=values.get("post.required_mentions", ()),
        disclosure_line=disclosure or None,
    )


def load_campaign(path) -> Campaign:
    """Read and validate a campaign TOML file, raising CampaignError on any problem."""
    path = Path(path).expanduser().resolve()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CampaignError(f"Campaign file not found: {path}") from None
    except OSError as e:
        raise CampaignError(f"Campaign file {path}: cannot read: {e}") from None
    if _toml is None:
        raise CampaignError(
            "Reading campaign TOML needs Python 3.11+ (tomllib) or `pip install tomli`."
        )
    try:
        data = _toml.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise CampaignError(f"Campaign file {path}: not UTF-8 text") from None
    except _toml.TOMLDecodeError as e:
        raise CampaignError(f"Campaign file {path}: invalid TOML: {e}") from None
    return parse_campaign(data, path)

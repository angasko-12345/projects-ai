"""Script generation: template rotation + claim/source validation."""
import json
import os
import re

from . import state as state_mod
from .providers_llm import make_provider
from .research import load_sources
from .state import PipelineError

_CLAIM_RE = re.compile(r"^\[C(\d+)\]\s*(.*)$")
_SOURCES_COMMENT_RE = re.compile(r"<!--\s*sources:\s*([S\d,\s]+)\s*-->")
_PAREN_RE = re.compile(r"\(([S\d,\s]+)\)\s*$")
_BRACKET_RE = re.compile(r"\[([S\d,\s]+)\]\s*$")
_TITLE_RE = re.compile(r"^Title:\s*(.*)$")
_ID_RE = re.compile(r"S\d+")


def recent_templates(runs_dir, window=5, exclude=None):
    """Last N templates in update order, optionally excluding one video."""
    found = []
    if not os.path.isdir(runs_dir):
        return found
    for vid in os.listdir(runs_dir):
        p = state_mod.state_path(runs_dir, vid)
        if os.path.isfile(p):
            try:
                s = state_mod.load(runs_dir, vid)
            except PipelineError:
                continue
            if s.get("template") and vid != exclude:
                found.append((s.get("updated_at", ""), s["template"]))
    found.sort(key=lambda t: t[0], reverse=True)
    return [t for _, t in found[:window]]


def pick_template(cfg, recent=None):
    """Pick a template differing from the last window templates.

    `recent` (list of names, newest first) overrides history lookup when given.
    """
    templates = cfg.get("script_templates") or []
    names = [t["name"] for t in templates if isinstance(t, dict) and t.get("name")]
    if not names:
        raise PipelineError("no script_templates configured")
    recent_names = list(recent) if recent else []
    window = int((cfg.get("thresholds") or {}).get("template_repeat_window", 5))
    blocked = set(recent_names[:window])
    for name in names:
        if name not in blocked:
            return name
    raise PipelineError(
        f"all {len(names)} templates used within last {window} videos; add more templates"
    )


def _extract_source_ids(text):
    m = _SOURCES_COMMENT_RE.search(text) or _PAREN_RE.search(text) or _BRACKET_RE.search(text)
    if not m:
        return [], text
    ids = _ID_RE.findall(m.group(1))
    clean = text[: m.start()].rstrip()
    return ids, clean


def parse_script(text):
    """Parse script text -> (title, claims[{id,text,sources}], needs_check[str])."""
    title = ""
    claims = []
    needs_check = []
    in_needs = False
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        t = _TITLE_RE.match(line.strip())
        if t:
            title = t.group(1).strip()
            continue
        c = _CLAIM_RE.match(line.strip())
        if c:
            ids, clean = _extract_source_ids(c.group(2).strip())
            claims.append({"id": f"C{c.group(1)}", "text": clean, "sources": ids})
            continue
        if line.strip().lower().startswith("needs check"):
            in_needs = True
            continue
        if line.strip().startswith("##"):
            in_needs = "check" in line.lower()
            continue
        if in_needs and line.strip().startswith("- "):
            needs_check.append(line.strip()[2:].strip())
    return title, claims, needs_check


def require_claim_sources(claims, source_ids):
    """Each claim needs >=2 valid source ids, else PipelineError."""
    valid = set(source_ids)
    for claim in claims:
        cited = [s for s in claim.get("sources", []) if s in valid]
        if len(cited) < 2:
            raise PipelineError(
                f"claim {claim.get('id', '?')} lacks >=2 sources (cited {claim.get('sources', [])})"
            )
    return True


def build_prompt(topic_title, template_sections, sources):
    lines = [
        f"Topic: {topic_title}",
        "Template sections: " + ", ".join(template_sections or []),
        "Sources:",
    ]
    for s in sources:
        lines.append(f"- {s.get('id')}: {s.get('title') or s.get('url')} — {s.get('note') or ''}")
    lines += [
        "Write one claim per line as [Cn] text (S1, S2) citing >=2 sources.",
        "Then a 'Title: ...' line and a 'Needs check:' list.",
    ]
    return "\n".join(lines)


def generate_script(runs_dir, video_id, cfg, provider=None):
    state = state_mod.load(runs_dir, video_id)
    sources = load_sources(runs_dir, video_id)
    if len(sources) < 2:
        raise PipelineError(f"need >=2 sources before script generation (have {len(sources)})")
    template = pick_template(cfg, recent=recent_templates(runs_dir, exclude=video_id))
    sections = []
    for t in cfg.get("script_templates") or []:
        if t.get("name") == template:
            sections = t.get("sections") or []
    provider = provider or make_provider(cfg)
    prompt = build_prompt(state.get("title", ""), sections, sources)
    text = provider.complete("You write concise factual video scripts.", prompt)
    title, claims, needs_check = parse_script(text)
    if not claims:
        raise PipelineError("script contained no [Cn] claims")
    require_claim_sources(claims, [s.get("id") for s in sources])
    title = title or state.get("title", "")
    md_lines = [f"# {title}", "", f"Template: {template}", ""]
    for section in sections:
        md_lines += [f"## {section}", ""]
    for claim in claims:
        md_lines.append(f"[{claim['id']}] {claim['text']} ({', '.join(claim['sources'])})")
    md_lines += ["", "## Sources", ""]
    for s in sources:
        md_lines.append(f"- {s.get('id')}: {s.get('url')} — {s.get('title', '')}")
    md_lines += ["", "## Needs human check", ""]
    for item in needs_check:
        md_lines.append(f"- {item}")
    if not needs_check:
        md_lines.append("- (none)")
    vdir = os.path.join(runs_dir, video_id)
    with open(os.path.join(vdir, "script.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    meta = {"template": template, "claims": claims, "needs_check": needs_check,
            "sources": [s.get("id") for s in sources], "title": title}
    with open(os.path.join(vdir, "script_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    state["template"] = template
    state["stage"] = "awaiting_script_approval"
    # Warn if estimated TTS duration deviates from target (Priority 3, item 12).
    target_min = float(cfg.get("target_length_minutes") or 0)
    if target_min > 0:
        # Estimate: 0.6s per fake chunk is not real; use word-count heuristic
        # (~150 wpm narration) on claim text.
        words = sum(len(c.get("text", "").split()) for c in claims)
        est_minutes = words / 150.0 if words else 0.0
        ratio = est_minutes / target_min if target_min else 0.0
        if ratio < 0.5 or ratio > 1.5:
            state["duration_warning"] = (
                f"estimated narration {est_minutes:.1f}min is "
                f"{'below 50%' if ratio < 0.5 else 'above 150%'} of "
                f"target {target_min}min (ratio {ratio:.2f}); verify script length"
            )
        else:
            state["duration_warning"] = None
    state_mod.save(runs_dir, video_id, state)
    return meta

"""Script generation: template rotation, section mapping, length contract,
claim/source validation and a lexical grounding pass.

The script is the contract every later stage depends on, so a script that is
far below the configured target length is a hard error, not a warning.
"""
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
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_ID_RE = re.compile(r"S\d+")
_WORD_RE = re.compile(r"[A-Za-z0-9'\u2019-]+")
_NUMBER_RE = re.compile(r"\b\d[\d,.]*\b")
_SOURCE_SECTION_NAMES = ("sources", "needs check", "needs human check")

DEFAULT_WORDS_PER_MINUTE = 150
MIN_LENGTH_RATIO = 0.6
WARN_LOW_RATIO = 0.85
WARN_HIGH_RATIO = 1.4

STOPWORDS = frozenset(["about", "above", "after", "again", "against", "almost", "along", "already", "also", "although", "among", "another", "because", "before", "being", "below", "between", "both", "came", "can", "could", "did", "does", "doing", "during", "each", "every", "few", "first", "from", "further", "had", "has", "have", "having", "here", "how", "into", "itself", "just", "like", "many", "more", "most", "much", "must", "near", "only", "other", "our", "out", "over", "own", "same", "should", "since", "some", "such", "than", "that", "the", "their", "theirs", "them", "then", "there", "these", "they", "this", "those", "through", "too", "under", "until", "very", "was", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "within", "without", "would", "year", "years"])


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
    """Pick a template differing from the last window templates."""
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


def count_words(text):
    return len(_WORD_RE.findall(text or ""))


def target_words(cfg):
    """Return (target_words, words_per_minute); (0, wpm) when no target is set."""
    minutes = float((cfg or {}).get("target_length_minutes") or 0)
    wpm = int((cfg or {}).get("words_per_minute") or DEFAULT_WORDS_PER_MINUTE)
    if minutes <= 0:
        return 0, wpm
    return round(minutes * wpm), wpm


def _extract_source_ids(text):
    m = _SOURCES_COMMENT_RE.search(text) or _PAREN_RE.search(text) or _BRACKET_RE.search(text)
    if not m:
        return [], text
    ids = _ID_RE.findall(m.group(1))
    clean = text[: m.start()].rstrip()
    return ids, clean


def parse_script_full(text):
    """Parse script text -> {title, claims, needs_check, sections}.

    Claims carry the section name they appeared under. Section headers are the
    template section names; "Sources" and "Needs check" end a section and are
    never treated as claims.
    """
    title = ""
    claims = []
    needs_check = []
    sections = []
    current = None
    in_needs = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        t = _TITLE_RE.match(stripped)
        if t:
            title = t.group(1).strip()
            continue
        header = _SECTION_RE.match(stripped)
        if header:
            name = header.group(1).strip()
            lowered = name.lower()
            if lowered.startswith(("source", "needs check", "needs human check")):
                in_needs = lowered.startswith("needs")
                current = None
            else:
                in_needs = False
                current = name
                if name not in sections:
                    sections.append(name)
            continue
        c = _CLAIM_RE.match(stripped)
        if c:
            ids, clean = _extract_source_ids(c.group(2).strip())
            claims.append({"id": f"C{c.group(1)}", "text": clean,
                           "sources": ids, "section": current})
            continue
        if stripped.lower().startswith("needs check"):
            in_needs = True
            continue
        if in_needs and stripped.startswith("- "):
            needs_check.append(stripped[2:].strip())
    return {"title": title, "claims": claims, "needs_check": needs_check,
            "sections": sections}


def parse_script(text):
    """Backward-compatible 3-tuple parse: (title, claims, needs_check)."""
    parsed = parse_script_full(text)
    return parsed["title"], parsed["claims"], parsed["needs_check"]


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


def build_prompt(topic_title, template_sections, sources, target=None, wpm=DEFAULT_WORDS_PER_MINUTE):
    sections = list(template_sections or [])
    lines = [
        f"Topic: {topic_title}",
        "Write a documentary narration script for a history YouTube video.",
        "Template sections: " + ", ".join(sections),
    ]
    if target:
        per_section = target // max(len(sections), 1)
        lines.append(
            f"Target length: {target} words total (about {per_section} per section, "
            f"{wpm} words per minute of narration).")
    lines += [
        "Requirements:",
        "- Open with a hook: a concrete question or a stake in the first 30 words.",
        "- Write narration for the ear. One idea per sentence. No semicolons. No bullet lists.",
        "- Use these section headers in order, and write nothing outside them: "
        + ", ".join(f"## {s}" for s in sections) + ".",
        "- Under each header, write one claim per line as: [Cn] <sentence> (S1, S2)",
        "- Every claim cites at least two source ids from the list below.",
        "- Do not invent numbers, dates, quotations or names. Leave out anything the sources do not support.",
        "Sources:",
    ]
    for s in sources:
        lines.append(f"- {s.get('id')}: {s.get('title') or s.get('url')} - {s.get('note') or ''}")
        excerpt = " ".join((s.get("text") or "").split())[:1200]
        if excerpt:
            lines.append(f"  Excerpt: {excerpt}")
    lines += [
        "Output shape, nothing else:",
        "Title: <title>",
    ]
    for section in sections:
        lines.append(f"## {section}")
        lines.append("[C1] <one sentence> (S1, S2)")
    lines += [
        "Needs check:",
        "- <item>",
    ]
    return "\n".join(lines)


def _numbers(text):
    found = set()
    for raw in _NUMBER_RE.findall(text or ""):
        digits = re.sub(r"[^\d]", "", raw)
        if digits:
            found.add(digits)
    return found


def _source_corpus(sources):
    parts = []
    for source in sources:
        text = (source.get("text") or "").strip()
        if not text:
            text = (source.get("note") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def assess_claim(claim, sources_by_id):
    """Lexical support check. A heuristic, labeled as such, never a truth claim.

    status:
      unchecked  - no cited source has stored text
      supported  - most distinctive words and all numbers appear in the corpus
      weak       - some overlap, or a number is missing
      unverified - little or no overlap, or a number is missing entirely
    """
    cited = [sources_by_id[s] for s in claim.get("sources", []) if s in sources_by_id]
    corpus = _source_corpus(cited)
    tiers = [s.get("tier") or "secondary" for s in cited]
    discovery_only = bool(tiers) and all(t == "discovery" for t in tiers)
    if not corpus:
        return {"status": "unchecked", "coverage": None, "missing_numbers": [],
                "discovery_only": discovery_only}
    corpus_words = set(_WORD_RE.findall(corpus.lower()))
    tokens = [w for w in _WORD_RE.findall(claim.get("text", "").lower())
              if len(w) >= 4 and w not in STOPWORDS]
    coverage = (sum(1 for w in tokens if w in corpus_words) / len(tokens)) if tokens else 0.0
    missing = sorted(_numbers(claim.get("text", "")) - _numbers(corpus))
    if missing:
        status = "weak" if coverage >= 0.55 else "unverified"
    elif coverage >= 0.55:
        status = "supported"
    elif coverage >= 0.3:
        status = "weak"
    else:
        status = "unverified"
    return {"status": status, "coverage": round(coverage, 3),
            "missing_numbers": missing, "discovery_only": discovery_only}


def _review_summary(claims):
    counts = {"supported": 0, "weak": 0, "unverified": 0, "unchecked": 0}
    for claim in claims:
        status = (claim.get("support") or {}).get("status", "unchecked")
        counts[status] = counts.get(status, 0) + 1
    missing_numbers = sorted({n for c in claims
                              for n in (c.get("support") or {}).get("missing_numbers", [])})
    discovery_only = [c["id"] for c in claims
                      if (c.get("support") or {}).get("discovery_only")]
    warnings = []
    if counts["unverified"]:
        warnings.append(
            f"{counts['unverified']} claim(s) have little lexical overlap with their cited sources")
    if counts["weak"]:
        warnings.append(
            f"{counts['weak']} claim(s) have only partial overlap with their cited sources")
    if missing_numbers:
        warnings.append(
            "numbers absent from cited source text: " + ", ".join(missing_numbers))
    if discovery_only:
        warnings.append(
            "claims citing only discovery-tier sources: " + ", ".join(discovery_only))
    if counts["unchecked"]:
        warnings.append(
            f"{counts['unchecked']} claim(s) could not be checked (cited sources have no stored text)")
    return {"counts": counts, "missing_numbers": missing_numbers,
            "discovery_only_claims": discovery_only, "warnings": warnings}


def format_review(meta):
    """Human-readable gate-1 review lines for the CLI."""
    review = (meta or {}).get("review") or {}
    counts = review.get("counts") or {}
    lines = [
        ("source grounding: "
        f"{counts.get('supported', 0)} supported, {counts.get('weak', 0)} weak, "
        f"{counts.get('unverified', 0)} unverified, {counts.get('unchecked', 0)} unchecked")
    ]
    for warning in review.get("warnings") or []:
        lines.append(f"WARN: {warning}")
    for warning in (meta or {}).get("length_warnings") or []:
        lines.append(f"WARN: {warning}")
    words = (meta or {}).get("word_count")
    target = (meta or {}).get("target_words")
    if words and target:
        lines.append(f"length: {words} words (target {target}, ratio {words / target:.2f})")
    return "\n".join(lines)


def _write_script_md(vdir, title, template, claims, sources, needs_check,
                     target, wpm):
    # Only sections that actually contain claims: a template header with no
    # narration is a lie about the script's structure.
    joined_sections = []
    for claim in claims:
        section = claim.get("section")
        if section and section not in joined_sections:
            joined_sections.append(section)
    lines = [f"# {title}", "", f"Template: {template}"]
    if target:
        lines.append(f"Target: {target} words")
        lines.append(f"Pace: {wpm} words per minute")
    lines.append("")
    if joined_sections:
        for section in joined_sections:
            lines += [f"## {section}", ""]
            for claim in claims:
                if claim.get("section") == section:
                    lines.append(
                        f"[{claim['id']}] {claim['text']} ({', '.join(claim['sources'])})")
            lines.append("")
    else:
        for claim in claims:
            lines.append(f"[{claim['id']}] {claim['text']} ({', '.join(claim['sources'])})")
        lines.append("")
    lines += ["## Sources", ""]
    for s in sources:
        lines.append(f"- {s.get('id')}: {s.get('url')} - {s.get('title', '')}")
    lines += ["", "## Needs human check", ""]
    for item in needs_check:
        lines.append(f"- {item}")
    if not needs_check:
        lines.append("- (none)")
    path = os.path.join(vdir, "script.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


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
    target, wpm = target_words(cfg)
    provider = provider or make_provider(cfg)
    prompt = build_prompt(state.get("title", ""), sections, sources, target=target, wpm=wpm)
    text = provider.complete(
        "You write concise factual video scripts. You never invent facts, numbers or quotes.",
        prompt)
    parsed = parse_script_full(text)
    claims = parsed["claims"]
    if not claims:
        raise PipelineError("script contained no [Cn] claims")
    require_claim_sources(claims, [s.get("id") for s in sources])
    if not parsed["sections"]:
        raise PipelineError(
            "script claims are not grouped under section headers; expected ## <section>")
    # Models often restart numbering per section (C1 in every heading). Claim
    # ids are keys for the scene/chunk mapping, so renumber across the script.
    for index, claim in enumerate(claims, 1):
        claim["id"] = f"C{index}"

    words = sum(count_words(c["text"]) for c in claims)
    ratio = (words / target) if target else None
    if target and words < target * MIN_LENGTH_RATIO:
        raise PipelineError(
            f"script is {words} words, below {int(MIN_LENGTH_RATIO * 100)}% of the "
            f"{target}-word target for {cfg.get('target_length_minutes')} min; "
            f"deficit {target - words} words. Re-run the script stage with a model "
            "that can produce the requested narration length.")
    warnings = []
    if target and ratio is not None and (ratio < WARN_LOW_RATIO or ratio > WARN_HIGH_RATIO):
        warnings.append(
            f"narration is {words} words, {ratio:.2f} of the {target}-word target")

    sources_by_id = {s.get("id"): s for s in sources}
    for claim in claims:
        claim["support"] = assess_claim(claim, sources_by_id)
    review = _review_summary(claims)
    title = parsed["title"] or state.get("title", "")
    vdir = os.path.join(runs_dir, video_id)
    _write_script_md(vdir, title, template, claims, sources,
                     parsed["needs_check"], target, wpm)

    section_order = [s for s in parsed["sections"]]
    for claim in claims:
        s = claim.get("section")
        if s and s not in section_order:
            section_order.append(s)
    meta = {
        "template": template,
        "claims": claims,
        "needs_check": parsed["needs_check"],
        "sources": [s.get("id") for s in sources],
        "title": title,
        "sections": [
            {"name": s, "claims": [c["id"] for c in claims if c.get("section") == s]}
            for s in section_order
        ],
        "word_count": words,
        "target_words": target or None,
        "words_per_minute": wpm,
        "review": review,
        "length_warnings": warnings,
    }
    with open(os.path.join(vdir, "script_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    state["template"] = template
    state["stage"] = "awaiting_script_approval"
    state["duration_warning"] = warnings[0] if warnings else None
    state_mod.save(runs_dir, video_id, state)
    return meta

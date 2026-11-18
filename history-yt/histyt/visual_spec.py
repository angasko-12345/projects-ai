"""Deterministic visual spec for a scene: type, archive queries, risk.

No network and no randomness. A spec depends only on the claim text, its
section, and the configured period and location, so a rerun builds the same
queries and resolves the same image. The manifest stores the result; later
stages never invent a query at render time.
"""
import re

STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing down during
each few for from further had has have having he her here hers him his how i if
in into is it its itself just me more most my no nor not now of off on once only
or other our out over own same she should so some such than that the their them
then there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your
""".split())

VISUAL_TYPES = ("archive", "generated", "map", "diagram", "card")

# Geography claims get a map; everything else starts from archival material.
MAP_HINTS = (
    "map", "border", "frontier", "route", "march", "campaign", "siege",
    "battle", "territory", "geography", "river", "coast", "crossed", "ford",
)

GENERIC_PERIOD_NOTE = (
    "verify costume, kit and architecture against the cited sources before release")

# Condensed from handbooks/ai-visual-production-handbook.md, part 4.
PERIOD_NOTES = (
    (("roman", "rome", "legion", "latin", "caesar"),
     "Roman: lorica segmentata and galea from the 1st century AD; Republican "
     "troops in mail with Montefortino helmets; no plate armour on foot soldiers"),
    (("greek", "grecian", "hoplite", "sparta", "athens", "macedon"),
     "Greek: linothorax or bronze muscle cuirass, Corinthian or Attic helmet, "
     "aspis; painted marble, not bare white"),
    (("egypt", "pharaoh", "nile", "ptolemaic", "pyramid"),
     "Egyptian: linen kilt, nemes headdress for royalty, shenti for others; "
     "painted temple walls; no anachronistic pyramid-building cliches"),
    (("medieval", "knight", "castle", "crusade", "viking", "feudal"),
     "Medieval: mail before plate, full plate only from about 1420; Romanesque "
     "arches before 1150, Gothic after"),
    (("japan", "samurai", "shogun", "edo", "sengoku", "ronin"),
     "Japanese: o-yoroi early, tosei-gusoku after the mid-16th century; kabuto "
     "with maedate; no Edo-period obi on Sengoku figures"),
    (("ww1", "world war 1", "world war i", "1914", "1918", "trench"),
     "WW1: horizon-blue French from 1915, field-grey German, khaki British with "
     "Brodie helmet from 1915, Stahlhelm from 1916"),
    (("ww2", "world war 2", "world war ii", "1939", "1945", "wehrmacht", "nazi"),
     "WW2: feldgrau and Stahlhelm for German troops, battledress and Brodie or "
     "MkII for British, olive drab and M1 for US; context the insignia"),
)

_NAME_PAIR = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")


def keywords(text, limit=8):
    """Content words from a claim, in reading order, lowercased and deduped."""
    out = []
    for match in re.findall(r"[A-Za-z][A-Za-z'-]+", text or ""):
        word = match.lower()
        if word in STOPWORDS or len(word) < 3 or word in out:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


def build_queries(narration, period, location, section):
    """Up to three archive queries: subject, material culture, era fallback.

    The narration itself is never sent: a sentence with "none returned" searches
    as a phrase and finds nothing. Queries are built from content words plus the
    configured period and location.
    """
    terms = keywords(narration, 8)
    queries = []

    def add(*parts):
        query = " ".join(str(part).strip().strip(",") for part in parts if part).strip()
        if query and query.lower() not in (q.lower() for q in queries):
            queries.append(query)

    add(" ".join(terms[:4]), period)
    add(" ".join(terms[-4:]), location)
    era = " ".join(str(part).strip() for part in (period, location) if part)
    add(era or section)
    return queries[:3]


def visual_type(narration, section, scene_cfg):
    """archive | generated | map | diagram | card, from config then text hints."""
    scene_cfg = scene_cfg or {}
    overrides = scene_cfg.get("visual_types") or {}
    if section and isinstance(overrides, dict):
        chosen = str(overrides.get(section) or "").strip().lower()
        if chosen in VISUAL_TYPES:
            return chosen
    global_choice = str(scene_cfg.get("visual_type") or "").strip().lower()
    if global_choice in VISUAL_TYPES:
        return global_choice
    text = (narration or "").lower()
    if any(hint in text for hint in MAP_HINTS):
        return "map"
    return "archive"


def period_note(period, location, narration):
    """Accuracy constraints for the depicted era, or a generic review reminder."""
    haystack = f"{period} {location} {narration}".lower()
    for keys, note in PERIOD_NOTES:
        if any(key in haystack for key in keys):
            return note
    return GENERIC_PERIOD_NOTE


def authenticity_risk(chosen_type, narration):
    """How likely a generated or archival asset is to be period-wrong.

    A generated still of a named person is the highest risk: the model invents a
    face for someone whose appearance is known or documented.
    """
    if chosen_type in ("map", "diagram", "card", "archive"):
        return "low"
    if _NAME_PAIR.search(narration or ""):
        return "high"
    return "medium"


def spec_for_scene(narration, section, period, location, cfg=None):
    """Full visual spec for one scene. Callers store it verbatim in the manifest."""
    scene_cfg = (cfg or {}).get("scene") or {}
    chosen_type = visual_type(narration, section, scene_cfg)
    queries = build_queries(narration, period, location, section)
    return {
        "visual_type": chosen_type,
        "visual_query": queries[0] if queries else "",
        "visual_queries": queries,
        "subject": " ".join(keywords(narration, 6)),
        "period_note": period_note(period, location, narration),
        "authenticity_risk": authenticity_risk(chosen_type, narration),
        "asset_status": "pending",
    }

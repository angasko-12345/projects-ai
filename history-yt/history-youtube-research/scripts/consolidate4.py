"""Consolidate v4: joined+total from best source (new about_full > legacy about-full > UNKNOWN)."""
import json
import os
import re

ROOT = r"D:\admin\code\projects\history-yt\history-youtube-research"
CH = ROOT + r"\evidence\channels"
AB = ROOT + r"\evidence\about_full"
AUDIT = json.load(open(CH + r"\audit.json", encoding="utf-8"))

def load(f):
    try: return json.load(open(f, encoding="utf-8-sig"))
    except Exception: return None

def parse_about(txt):
    """Return (joined, subs, vids, total) from about-page innerText stats block."""
    if not txt: return None, None, None, None
    joined = subs = vids = total = None
    ms = list(re.finditer(r"Joined\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", txt))
    if ms:
        joined = ms[-1].group(1); tail = txt[ms[-1].end():]
        m = re.search(r"([\d.]+[KMB]?)\s*subscribers", tail)
        if m: subs = m.group(1)
        m = re.search(r"([\d,]+)\s*videos", tail)
        if m: vids = m.group(1)
        m = re.search(r"([\d,]+)\s*views", tail)
        if m: total = m.group(1)
    return joined, subs, vids, total

out = {}
for slug, info in sorted(AUDIT.items()):
    lat = load(f"{CH}\\{slug}-latest.json") or {}
    header = (lat.get("headerText") or "").replace("\n", " ")
    chan = lat.get("channel") or {}

    # about sources in priority order
    j = s2 = v2 = t2 = None
    for src in [f"{AB}\\{slug}.txt", f"{CH}\\{slug}-about-full.txt"]:
        if os.path.exists(src):
            j, s2, v2, t2 = parse_about(open(src, encoding="utf-8-sig").read())
            if j: break
    subs = s2 if s2 else None
    if subs is None:
        m = re.search(r"([\d.]+[KMB]?)\s*subscribers", header)
        subs = m.group(1) if m else "UNKNOWN"

    good = info.get("good")
    pop = load(f"{CH}\\{good}") if good else None
    pop_vids = (pop or {}).get("videos") or []
    lat_vids = lat.get("videos") or []
    newest = lat_vids[0] if lat_vids else {}

    out[slug] = {
        "title": chan.get("title", "UNKNOWN"),
        "channel_id": chan.get("id", "UNKNOWN"),
        "handle": slug,
        "subscribers_shown": subs,
        "exact_video_count": v2 or "UNKNOWN",
        "joined": j or "UNKNOWN",
        "channel_total_views": t2 or "UNKNOWN",
        "recent_n": len(lat_vids),
        "newest_upload": {"title": newest.get("title", "UNKNOWN"), "parts": newest.get("parts", "UNKNOWN")},
        "popular_top5": [{"title": v.get("title"), "parts": v.get("parts"), "id": v.get("id")} for v in pop_vids[:5]],
        "popular_n": len(pop_vids),
        "recent_videos": [{"title": v.get("title"), "parts": v.get("parts"), "length": v.get("length"),
                           "published": v.get("published"), "id": v.get("id")} for v in lat_vids],
        "popular_source": good,
    }

# K&G manual
kg_lat = load(CH + r"\kings-and-generals.json") or {}
kg_pop = load(CH + r"\kings-and-generals-popular.json") or {}
kg_header = (kg_lat.get("headerText") or "").replace("\n", " ")
kg_chan = kg_lat.get("channel") or {}
kg_av = kg_lat.get("videos") or []
kg_newest = kg_av[0] if kg_av else {}
j, s2, v2, t2 = parse_about(open(f"{AB}\\kings-and-generals.txt", encoding="utf-8-sig").read())
out["kings-and-generals"] = {
    "title": kg_chan.get("title", "UNKNOWN"), "channel_id": kg_chan.get("id", "UNKNOWN"),
    "handle": "kings-and-generals",
    "subscribers_shown": s2 or (re.search(r"([\d.]+[KMB]?)\s*subscribers", kg_header).group(1) if re.search(r"([\d.]+[KMB]?)\s*subscribers", kg_header) else "UNKNOWN"),
    "exact_video_count": v2 or "UNKNOWN", "joined": j or "UNKNOWN", "channel_total_views": t2 or "UNKNOWN",
    "recent_n": len(kg_av),
    "newest_upload": {"title": kg_newest.get("title", "UNKNOWN"), "parts": kg_newest.get("parts", "UNKNOWN")},
    "popular_top5": [{"title": v.get("title"), "parts": v.get("parts"), "id": v.get("id")} for v in (kg_pop.get("videos") or [])[:5]],
    "popular_n": len(kg_pop.get("videos") or []),
    "recent_videos": [{"title": v.get("title"), "parts": v.get("parts"), "length": v.get("length"),
                       "published": v.get("published"), "id": v.get("id")} for v in kg_av],
    "popular_source": "kings-and-generals-popular.json (manual sort, main session)",
}

json.dump(out, open(CH + r"\consolidated.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"consolidated {len(out)} channels (v4)")
miss = []
for s, r in out.items():
    flag = "  <-- MISSING" if r['joined'] == 'UNKNOWN' else ""
    if r['joined'] == 'UNKNOWN': miss.append(s)
    print(f"{s:28} subs={r['subscribers_shown']:>7} joined={r['joined']:>14} total={r['channel_total_views']:>12} vids={r['exact_video_count']:>6}{flag}")
print("\nMissing joined:", miss)

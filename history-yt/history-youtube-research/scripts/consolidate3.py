"""Consolidate evidence v3: joined + total + exact_vids via LAST occurrence.
Also merge K&G and other manual files. Writes consolidated.json + about_overrides.json."""
import json, os, re, glob

CH = r"D:\admin\code\projects\history-yt\history-youtube-research\evidence\channels"
AUDIT = json.load(open(CH + r"\audit.json", encoding="utf-8"))

def load(f):
    try: return json.load(open(f, encoding="utf-8-sig"))
    except Exception: return None

out = {}
overrides = {}
for slug, info in sorted(AUDIT.items()):
    lat = load(f"{CH}\\{slug}-latest.json") or {}
    header = (lat.get("headerText") or "").replace("\n", " ")
    chan = lat.get("channel") or {}
    good = info.get("good")
    pop = load(f"{CH}\\{good}") if good else None
    pop_vids = (pop or {}).get("videos") or []

    joined = total = exact_vids = "UNKNOWN"
    abf = f"{CH}\\{slug}-about-full.txt"
    if info.get("about_full"):
        txt = open(abf, encoding="utf-8-sig").read()
        # find last "Joined" occurrence; the stats block is at the very end
        ms = list(re.finditer(r"Joined\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", txt))
        if ms:
            joined = ms[-1].group(1); start = ms[-1].end()
            tail = txt[start:]
            m = re.search(r"([\d.]+[KMB]?)\s*subscribers", tail)
            if m: overrides[slug] = {"_subs_tail": m.group(1)}
            m = re.search(r"([\d,]+)\s*videos", tail)
            if m: exact_vids = m.group(1)
            m = re.search(r"([\d,]+)\s*views", tail)
            if m: total = m.group(1)

    subs_m = re.search(r"([\d.]+[KMB]?)\s*subscribers", header)
    subs = overrides.get(slug, {}).get("_subs_tail") if slug in overrides else None
    if not subs:
        subs = subs_m.group(1) if subs_m else "UNKNOWN"
    else:
        subs = subs
    vc_m = re.search(r"([\d,]+\s*[KMB]?)\s*videos", header)
    lat_vids = lat.get("videos") or []
    newest = lat_vids[0] if lat_vids else {}

    out[slug] = {
        "title": chan.get("title", "UNKNOWN"),
        "channel_id": chan.get("id", "UNKNOWN"),
        "handle": slug,
        "subscribers_shown": subs,
        "video_count_shown": vc_m.group(1).replace(" ", "") if vc_m else "UNKNOWN",
        "exact_video_count": exact_vids,
        "joined": joined,
        "channel_total_views": total,
        "recent_n": len(lat_vids),
        "newest_upload": {"title": newest.get("title", "UNKNOWN"), "parts": newest.get("parts", "UNKNOWN")},
        "popular_top5": [{"title": v.get("title"), "parts": v.get("parts"), "id": v.get("id")} for v in pop_vids[:5]],
        "popular_n": len(pop_vids),
        "recent_videos": [{"title": v.get("title"), "parts": v.get("parts"), "length": v.get("length"),
                           "published": v.get("published"), "id": v.get("id")} for v in lat_vids],
        "popular_source": good,
        "header_excerpt": header[:200],
    }

# Manual Kings and Generals (early manual extraction; rename to standard slug)
kg_lat = load(CH + r"\kings-and-generals.json") or {}
kg_pop = load(CH + r"\kings-and-generals-popular.json") or {}
kg_header = (kg_lat.get("headerText") or "").replace("\n", " ")
kg_chan = kg_lat.get("channel") or {}
kg_nv = kg_lat.get("videos") or []
kg_newest = kg_nv[0] if kg_nv else {}
out["kings-and-generals"] = {
    "title": kg_chan.get("title", "UNKNOWN"),
    "channel_id": kg_chan.get("id", "UNKNOWN"),
    "handle": "kings-and-generals",
    "subscribers_shown": (re.search(r"([\d.]+[KMB]?)\s*subscribers", kg_header) or [None, "UNKNOWN"])[1] if re.search(r"([\d.]+[KMB]?)\s*subscribers", kg_header) else "UNKNOWN",
    "video_count_shown": (re.search(r"([\d,]+\s*[KMB]?)\s*videos", kg_header) or [None, "UNKNOWN"])[1] if re.search(r"([\d,]+\s*[KMB]?)\s*videos", kg_header) else "UNKNOWN",
    "exact_video_count": "UNKNOWN", "joined": "UNKNOWN", "channel_total_views": "UNKNOWN",
    "recent_n": len(kg_nv),
    "newest_upload": {"title": kg_newest.get("title", "UNKNOWN"), "parts": kg_newest.get("parts", "UNKNOWN")},
    "popular_top5": [{"title": v.get("title"), "parts": v.get("parts"), "id": v.get("id")} for v in (kg_pop.get("videos") or [])[:5]],
    "popular_n": len(kg_pop.get("videos") or []),
    "recent_videos": [{"title": v.get("title"), "parts": v.get("parts"), "length": v.get("length"),
                       "published": v.get("published"), "id": v.get("id")} for v in kg_nv],
    "popular_source": "kings-and-generals-popular.json (sorted by manual click in main session)",
    "header_excerpt": kg_header[:200],
}

json.dump(out, open(CH + r"\consolidated.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
json.dump(overrides, open(CH + r"\about_overrides.json", "w", encoding="utf-8"), indent=2)
print(f"consolidated {len(out)} channels (v3)")
miss = []
for s, r in out.items():
    flag = "  <-- MISSING joined" if r['joined'] == 'UNKNOWN' else ""
    if r['joined'] == 'UNKNOWN': miss.append(s)
    print(f"{s:28} subs={r['subscribers_shown']:>7} joined={r['joined']:>14} total={r['channel_total_views']:>12} vids={r['exact_video_count']:>6}{flag}")
print("\nMissing joined:", miss)

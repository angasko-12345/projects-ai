"""Consolidate evidence: pick verified popular file, parse joined/views from about-full."""
import json, glob, os, re, shutil

CH = r"D:\admin\code\projects\history-yt\history-youtube-research\evidence\channels"
AUDIT = json.load(open(CH + r"\audit.json", encoding="utf-8"))

def load(f):
    try: return json.load(open(f, encoding="utf-8-sig"))
    except Exception: return None

out = {}
for slug, info in sorted(AUDIT.items()):
    lat = load(f"{CH}\\{slug}-latest.json") or {}
    header = (lat.get("headerText") or "").replace("\n", " ")
    chan = lat.get("channel") or {}

    # good popular file
    good = info.get("good")
    pop = load(f"{CH}\\{good}") if good else None
    pop_vids = (pop or {}).get("videos") or []

    # about-full parsing
    abf = f"{CH}\\{slug}-about-full.txt"
    joined = total = exact_vids = "UNKNOWN"
    if info.get("about_full"):
        txt = open(abf, encoding="utf-8-sig").read()
        m = re.search(r"Joined\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", txt)
        if m: joined = m.group(1)
        m = re.search(r"([\d,]{6,})\s*views", txt)
        if m: total = m.group(1)
        m = re.search(r"\n\s*([\d,]+)\s*videos\s*\n", txt)
        if m: exact_vids = m.group(1)

    subs_m = re.search(r"([\d.]+[KMB]?)\s*subscribers", header)
    vc_m = re.search(r"([\d,]+\s*[KMB]?)\s*videos", header)
    lat_vids = lat.get("videos") or []
    newest = lat_vids[0] if lat_vids else {}

    out[slug] = {
        "title": chan.get("title", "UNKNOWN"),
        "channel_id": chan.get("id", "UNKNOWN"),
        "handle": slug,
        "subscribers_shown": subs_m.group(1) if subs_m else "UNKNOWN",
        "video_count_shown": vc_m.group(1).replace(" ", "") if vc_m else "UNKNOWN",
        "exact_video_count": exact_vids,
        "joined": joined,
        "channel_total_views": total,
        "recent_n": len(lat_vids),
        "newest_upload": {"title": newest.get("title","UNKNOWN"), "parts": newest.get("parts","UNKNOWN")},
        "popular_top5": [{"title": v.get("title"), "parts": v.get("parts"), "id": v.get("id")} for v in pop_vids[:5]],
        "popular_n": len(pop_vids),
        "recent_videos": [{"title": v.get("title"), "parts": v.get("parts"), "length": v.get("length"),
                           "published": v.get("published"), "id": v.get("id")} for v in lat_vids],
        "popular_source": good,
        "header_excerpt": header[:200],
    }

json.dump(out, open(CH + r"\consolidated.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"consolidated {len(out)} channels -> consolidated.json")
for s, r in out.items():
    print(f"{s:28} subs={r['subscribers_shown']:>7} joined={r['joined']:>14} total={r['channel_total_views']:>11} popsrc={r['popular_source']}")

"""Consolidate evidence (v2): %m patterns fixed; joined/total via last-match on about-full."""
import json
import re

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
    good = info.get("good")
    pop = load(f"{CH}\\{good}") if good else None
    pop_vids = (pop or {}).get("videos") or []

    joined = total = exact_vids = "UNKNOWN"
    abf = f"{CH}\\{slug}-about-full.txt"
    if info.get("about_full"):
        txt = open(abf, encoding="utf-8-sig").read()
        # channel stats block sits at the very end: "Joined X\nN subscribers\nN videos\nN views"
        m = re.search(r"Joined\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s*\n([\d,\.]+[KMB]?)\s*subscribers\s*\n([\d,]+)\s*videos\s*\n([\d,]+)\s*views", txt)
        if m:
            joined, total, exact_vids = m.group(1), m.group(4), m.group(3)
        else:
            m2 = re.search(r"Joined\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", txt)
            if m2: joined = m2.group(1)

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
        "newest_upload": {"title": newest.get("title", "UNKNOWN"), "parts": newest.get("parts", "UNKNOWN")},
        "popular_top5": [{"title": v.get("title"), "parts": v.get("parts"), "id": v.get("id")} for v in pop_vids[:5]],
        "popular_n": len(pop_vids),
        "recent_videos": [{"title": v.get("title"), "parts": v.get("parts"), "length": v.get("length"),
                           "published": v.get("published"), "id": v.get("id")} for v in lat_vids],
        "popular_source": good,
        "header_excerpt": header[:200],
    }

json.dump(out, open(CH + r"\consolidated.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"consolidated {len(out)} channels (v2)")
for s, r in out.items():
    print(f"{s:28} subs={r['subscribers_shown']:>7} joined={r['joined']:>14} total={r['channel_total_views']:>11}")
print("\nMissing joined/total:", [s for s,r in out.items() if r['joined']=='UNKNOWN'])

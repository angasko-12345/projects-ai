"""Build data/channel_benchmark.csv (+ findings summary numbers) from consolidated.json
Adds derived columns: views/subscriber ratio, views per video, recent median views.
"""
import csv
import json
import re
import statistics

ROOT = r"D:\admin\code\projects\history-yt\history-youtube-research"
data = json.load(open(ROOT + r"\evidence\channels\consolidated.json", encoding="utf-8"))

def num(s):
    if not s or s == "UNKNOWN": return None
    s = s.replace(",", "").upper()
    m = re.match(r"([\d.]+)([KMB])?", s)
    if not m: return None
    n = float(m.group(1)); u = m.group(2) or ""
    return n * {"K": 1e3, "M": 1e6, "B": 1e9, "": 1}[u]

def views_of(v):
    p = (v.get("parts") or "")
    m = re.search(r"([\d.]+[KMB]?)", p.replace(",", ""))
    # first token before | is usually view count "264K | 3d ago" or "1d ago"
    parts = [x.strip() for x in p.split("|")]
    tok = parts[0] if parts else ""
    m = re.match(r"([\d.]+[KMB]?)$", tok)
    if not m: return None
    return num(m.group(1))

rows = []
for slug, c in sorted(data.items()):
    subs_n = num(c["subscribers_shown"])
    total_n = num(c["channel_total_views"])
    vids_n = num(c["exact_video_count"])
    recent_views = [views_of(v) for v in c["recent_videos"]]
    recent_views = [x for x in recent_views if x is not None]
    ratios = (total_n / subs_n) if (subs_n and total_n) else None
    vpvideo = (total_n / vids_n) if (vids_n and total_n) else None
    lengths = []
    for v in c["recent_videos"]:
        L = v.get("length") or ""
        m = re.match(r"(\d+):(\d+)", L)
        if m: lengths.append(int(m.group(1)) * 60 + int(m.group(2)))
    top1 = c["popular_top5"][0] if c["popular_top5"] else {}
    rows.append({
        "slug": slug,
        "channel_title": c["title"],
        "channel_id": c["channel_id"],
        "subscribers_shown_2026-11-05": c["subscribers_shown"],
        "exact_video_count": c["exact_video_count"],
        "joined": c["joined"],
        "channel_total_views_2026-11-05": c["channel_total_views"],
        "views_per_subscriber": round(ratios) if ratios else "UNKNOWN",
        "avg_views_per_video": round(vpvideo) if vpvideo else "UNKNOWN",
        "recent_videos_extracted": c["recent_n"],
        "recent_views_median": round(statistics.median(recent_views)) if recent_views else "UNKNOWN",
        "recent_views_mean": round(statistics.mean(recent_views)) if recent_views else "UNKNOWN",
        "recent_typical_length_s": round(statistics.median(lengths)) if lengths else "UNKNOWN",
        "newest_upload_title": c["newest_upload"].get("title", ""),
        "newest_upload_age": c["newest_upload"].get("parts", ""),
        "popular_top1_title": top1.get("title", ""),
        "popular_top1_parts": top1.get("parts", ""),
        "popular_top1_id": top1.get("id", ""),
        "popular_top5_json": json.dumps(c["popular_top5"], ensure_ascii=False),
        "evidence_trust": "VERIFIED (browser-extracted 2026-11-05)",
    })

with open(ROOT + r"\data\channel_benchmark.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
print(f"wrote {len(rows)} rows -> data/channel_benchmark.csv")
for r in rows:
    print(f"{r['slug']:28} {r['subscribers_shown_2026-11-05']:>7} total={r['channel_total_views_2026-11-05']:>12} vpSub={r['views_per_subscriber']!s:>7} avgVid={r['avg_views_per_video']!s:>10} medRecent={r['recent_views_median']!s:>8} medLenS={r['recent_typical_length_s']}")

"""Normalize channel evidence JSON -> data/channel_benchmark.csv
Run after all benchmark lanes finish. Reads ONLY files present; missing = UNKNOWN.
"""
import csv
import glob
import json
import os
import re
import statistics

CH = r"D:\admin\code\projects\history-yt\history-youtube-research\evidence\channels"
OUT = r"D:\admin\code\projects\history-yt\history-youtube-research\data"
os.makedirs(OUT, exist_ok=True)

def parse_views(s):
    if not s: return None
    s = s.strip()
    m = re.match(r"([\d.]+)\s*([KMBkmb])?", s.replace(",", ""))
    if not m: return None
    n = float(m.group(1)); unit = (m.group(2) or "").upper()
    return int(n * {"K": 1e3, "M": 1e6, "B": 1e9, "": 1}[unit])

def load_json(path):
    if not os.path.exists(path): return None
    try:
        with open(path, encoding="utf-8-sig") as f: return json.load(f)
    except Exception as e:
        print(f"PARSE FAIL {path}: {e}"); return None

latest = {}
for f in glob.glob(CH + r"\*-latest.json"):
    latest[os.path.basename(f)[:-len("-latest.json")]] = f

rows = []
for slug, lf in sorted(latest.items()):
    d = load_json(lf)
    pf = CH + "\\" + slug + "-popular.json"
    pop = load_json(pf) if os.path.exists(pf) else None
    about_f = CH + "\\" + slug + "-about.txt"
    about = open(about_f, encoding="utf-8").read() if os.path.exists(about_f) else ""

    header = (d or {}).get("headerText", "") or ""
    channel = (d or {}).get("channel", {}) or {}
    subs_m = re.search(r"([\d.]+[KMB]?)\s*subscribers", header)
    subs = subs_m.group(1) if subs_m else "UNKNOWN"
    vc_m = re.search(r"([\d,\.]+[KMB]?)\s*videos", header)
    vidcount = vc_m.group(1) if vc_m else "UNKNOWN"
    joined_m = re.search(r"Joined\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", about)
    joined = joined_m.group(1) if joined_m else "UNKNOWN"
    totalviews_m = re.search(r"([\d,\.]+)\s*views", about)
    totalviews = totalviews_m.group(1) if totalviews_m else "UNKNOWN"

    videos = (d or {}).get("videos", [])
    def part(v):
        return v.get("parts", "") or ""
    v_counts = []
    for v in videos:
        m = re.search(r"([\d.]+[KMB]?)", part(v))
        if m and re.search(r"view", part(v), re.IGNORECASE) or (m and "ago" in part(v) and "|" in part(v)):
            vv = parse_views(m.group(1))
            if vv: v_counts.append(vv)
    ages = [v.get("published", "") for v in videos]
    newest = ages[0] if ages else "UNKNOWN"
    # rough upload cadence: ages list is recent-first
    cadence = "UNKNOWN"
    if len(videos) >= 10:
        cadence = f"{len(videos)} videos in window shown (newest: {newest})"

    popvids = (pop or {}).get("videos", []) if pop else []
    top3 = " || ".join(f"{v.get('title','?')} ({(v.get('parts') or '').split('|')[0].strip()})" for v in popvids[:3])
    med = f"{int(statistics.median(v_counts)):,}" if v_counts else "UNKNOWN"
    avg = f"{int(statistics.mean(v_counts)):,}" if v_counts else "UNKNOWN"
    mx = f"{max(v_counts):,}" if v_counts else "UNKNOWN"

    rows.append({
        "slug": slug,
        "channel_title": channel.get("title", "UNKNOWN"),
        "channel_id": channel.get("id", "UNKNOWN"),
        "subscribers_shown": subs,
        "video_count_shown": vidcount,
        "joined": joined,
        "total_views_about": totalviews,
        "recent_videos_extracted": len(videos),
        "recent_views_median": med,
        "recent_views_avg": avg,
        "recent_views_max": mx,
        "newest_upload": newest,
        "popular_top3": top3,
        "about_excerpt": about.replace("\n", " / ")[:600],
    })

with open(OUT + r"\channel_benchmark.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["slug"])
    w.writeheader(); w.writerows(rows)
with open(OUT + r"\channel_benchmark.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, indent=2, ensure_ascii=False)
print(f"wrote {len(rows)} channel rows -> {OUT}\\channel_benchmark.csv")
for r in rows:
    print(f"{r['slug']:32} subs={r['subscribers_shown']:>8} vids={r['video_count_shown']:>6} recentN={r['recent_videos_extracted']:>2} median={r['recent_views_median']}")

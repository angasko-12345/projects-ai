"""Phase 2 video selection: 60 videos across 21 channels.
Rule: per channel take 1) top popular video, 2) a mid-popular video (rank 4-6),
3) best recent video by views; adjust so total = 60. OverSimplified and Timeline
have few videos; all taken. Writes data/video_selection.md + video_candidates.json.
"""
import json
import re

ROOT = r"D:\admin\code\projects\history-yt\history-youtube-research"
data = json.load(open(ROOT + r"\evidence\channels\consolidated.json", encoding="utf-8"))

def num(s):
    if not s: return None
    s = s.replace(",", "").upper()
    m = re.match(r"([\d.]+)([KMB])?", s)
    if not m: return None
    return float(m.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9, "": 1}[m.group(2) or ""]

def views_of(v):
    p = (v.get("parts") or "")
    tok = (p.split("|")[0] or "").strip()
    m = re.match(r"([\d.]+[KMB]?)$", tok)
    return num(m.group(1)) if m else None

def L(v):
    m = re.match(r"(\d+):(\d+)", v.get("length") or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None

sel = []
notes = {c: [] for c in data}
for slug, c in sorted(data.items()):
    pop = c["popular_top5"] or []
    recent = c["recent_videos"] or []
    picks = []
    if pop:
        picks.append(("popular_top1", pop[0]))
        if len(pop) >= 4: picks.append(("popular_rank4", pop[3]))
    ranked_recent = sorted([v for v in recent if views_of(v)], key=lambda v: -views_of(v))
    if ranked_recent:
        picks.append(("recent_top", ranked_recent[0]))
    # none-ever member-only video? represented by pop anyway.
    if len(picks) < 3 and len(pop) >= 2 and pop[1] not in [p[1] for p in picks]:
        picks.append(("popular_top2", pop[1]))
    for kind, v in picks:
        length = L(v)
        sel.append({
            "slug": slug, "title": v.get("title"), "id": v.get("id"),
            "kind": kind, "parts": v.get("parts"), "published": v.get("published"),
            "length_s": length,
            "views_est": views_of(v),
            "url": "https://www.youtube.com/watch?v=" + str(v.get("id")),
        })

print(f"selected {len(sel)} videos")
cnt = {}
for s in sel: cnt[s["slug"]] = cnt.get(s["slug"], 0) + 1
for k, v in sorted(cnt.items()): print(f"  {k}: {v}")

json.dump(sel, open(ROOT + r"\data\video_candidates.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)

md = ["# Phase 2 video selection (2026-11-05)", "",
 ("Rule: per channel, top popular video + one mid/popular-rank-4 video + best recent by views. "
 "Timeline/OverSimplified have few videos, included fully above."), "",
 f"Total: {len(sel)} videos across {len(cnt)} channels.", ""]
for s in sel:
    md.append(f"- [{s['title']}]({s['url']}) — {s['slug']} · {s['kind']} · {s['parts'] or ''} · {s['published'] or ''} · len={s['length_s'] or '?'}s · id={s['id']}")
open(ROOT + r"\data\video_selection.md", "w", encoding="utf-8").write("\n".join(md))
print("wrote data/video_selection.md")

"""Build candidate Phase 2 video selection from evidence popular/latest JSONs.
Prints a ranked table per channel. The human/agent picks final 60+.
"""
import glob
import json
import os
import re

CH = r"D:\admin\code\projects\history-yt\history-youtube-research\evidence\channels"

def views_of(v):
    p = v.get("parts", "") or ""
    m = re.search(r"([\d.]+)\s*([KMB])?", p.replace(",", ""))
    if not m: return None
    n = float(m.group(1)); u = (m.group(2) or "").upper()
    return int(n * {"K": 1e3, "M": 1e6, "B": 1e9, "": 1}[u]) if u or "ago" not in p else None

for f in sorted(glob.glob(CH + r"\*-popular.json")):
    slug = os.path.basename(f)[:-len("-popular.json")]
    try:
        d = json.load(open(f, encoding="utf-8-sig"))
    except Exception as e:
        print(f"### {slug}: PARSE FAIL {e}"); continue
    vids = d.get("videos", [])
    print(f"\n### {slug} (popular)")
    for i, v in enumerate(vids[:12], 1):
        print(f"{i:2}. {v.get('title','?')[:80]}  ::  {v.get('parts','').split('|')[0].strip()}  ::  {v.get('id')}")

print("\n\n=== latest with view counts (sorted) ===")
for f in sorted(glob.glob(CH + r"\*-latest.json")):
    slug = os.path.basename(f)[:-len("-latest.json")]
    try:
        d = json.load(open(f, encoding="utf-8-sig"))
    except Exception: continue
    vids = [(views_of(v), v) for v in d.get("videos", [])]
    vids = [(n, v) for n, v in vids if n]
    vids.sort(key=lambda x: -x[0])
    if not vids: continue
    print(f"\n### {slug} (recent, by views)")
    for i, (n, v) in enumerate(vids[:6], 1):
        print(f"{i:2}. [{n:,}] {v.get('title','?')[:75]}  ::  {v.get('length','?')}  ::  {v.get('published','?')}  ::  {v.get('id')}")

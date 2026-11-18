"""Regenerate audit.json from current on-disk evidence (verified popular file per channel)."""
import glob
import json
import os

ROOT = r"D:\admin\code\projects\history-yt\history-youtube-research"
CH = ROOT + r"\evidence\channels"
AB = ROOT + r"\evidence\about_full"

def load(f):
    try: return json.load(open(f, encoding="utf-8-sig"))
    except Exception: return None

def fid(p):
    d = load(p)
    v = (d or {}).get("videos") or []
    return v[0]["id"] if v else None

slugs = sorted(os.path.basename(f)[:-len("-latest.json")] for f in glob.glob(CH + r"\*-latest.json"))
audit = {}
print("audit regenerated; NOT-SORTED:", [s for s,a in audit.items() if a["status"]!="OK: popular.json sorted"] if (audit := audit) else "")
for s in slugs:
    lat = f"{CH}\\{s}-latest.json"
    lat_id = fid(lat)
    good = None
    for cand in [f"{CH}\\{s}-popular-manual.json", f"{CH}\\{s}-popular.json"]:
        if os.path.exists(cand):
            cid = fid(cand)
            if cid and cid != lat_id:
                good = os.path.basename(cand); break
    abf = f"{CH}\\{s}-about-full.txt"
    has_full = (os.path.exists(abf) and os.path.getsize(abf) > 2000) or os.path.exists(f"{AB}\\{s}.txt")
    audit[s] = {"status": "OK: popular.json sorted" if good else "NOT-SORTED: no verified popular file",
                "good": good, "about_full": has_full, "n_latest": 0, "header": ""}
json.dump(audit, open(CH + "\\audit.json", "w"), indent=2)
print("audit regenerated; NOT-SORTED:", [s for s,a in audit.items() if a["status"]!="OK: popular.json sorted"])

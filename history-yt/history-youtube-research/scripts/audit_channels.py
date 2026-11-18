"""Audit channel evidence: which popular files are truly Popular-sorted."""
import json, glob, os

CH = r"D:\admin\code\projects\history-yt\history-youtube-research\evidence\channels"

def load(f):
    try:
        return json.load(open(f, encoding="utf-8-sig"))
    except Exception:
        return None

def first_id(d):
    if not d: return None
    v = d.get("videos") or []
    return v[0]["id"] if v else None

def count(d):
    return len((d or {}).get("videos") or [])

slugs = sorted(x[:-len("-latest.json")] for x in
               (os.path.basename(f) for f in glob.glob(CH + r"\*-latest.json")))
print(f"slugs: {len(slugs)}")
results = {}
for s in slugs:
    lat = load(f"{CH}\\{s}-latest.json")
    pop = load(f"{CH}\\{s}-popular.json")
    man = load(f"{CH}\\{s}-popular-manual.json") if os.path.exists(f"{CH}\\{s}-popular-manual.json") else None
    ab_f = f"{CH}\\{s}-about-full.txt"
    has_full = os.path.exists(ab_f) and os.path.getsize(ab_f) > 2000

    lat_id, pop_id, man_id = first_id(lat), first_id(pop), first_id(man)

    # Decide which file is truly popular-sorted:
    # - a file is popular-sorted if its first id != latest first id
    pop_ok = pop_id is not None and pop_id != lat_id
    man_ok = man is not None and man_id != lat_id
    if man_ok:
        status = "NEEDS-REPLACE: use -popular-manual.json"
        good = f"{s}-popular-manual.json"
    elif pop_ok:
        status = "OK: popular.json sorted"
        good = f"{s}-popular.json"
    else:
        status = "NOT-SORTED: no verified popular file"
        good = None
    results[s] = {"status": status, "good": good, "about_full": has_full,
                  "n_latest": count(lat), "subs_head": None}
    # subs from headerText
    try:
        ht = (lat.get("headerText") or "").replace("\n", " ")
        results[s]["header"] = ht[:160]
    except Exception:
        pass
    print(f"{s:28} latest_n={count(lat):2} full_about={has_full!s:5} {status}" + (f" -> {good}" if good else ""))

json.dump(results, open(CH + r"\audit.json", "w"), indent=2)
print("\nwrote audit.json")
print("\nNeeds popular fix:", [s for s, r in results.items() if r["status"].startswith("NOT-SORTED")])
print("Needs replace:", [s for s, r in results.items() if r["status"].startswith("NEEDS")])

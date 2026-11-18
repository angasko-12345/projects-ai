import csv
import glob
import json
import os
import re
import statistics

root = r"D:\admin\code\projects\history-yt\history-youtube-research"
cand_file = root + r"\data\video_candidates.json"
meta_dir = root + r"\data\video_meta"
cons = json.load(open(root + r"\evidence\channels\consolidated.json", encoding="utf-8"))
cands = json.load(open(cand_file, encoding="utf-8"))
by_id = {}
for x in cands:
    if x["id"] not in by_id or (x.get("published") and not by_id[x["id"]].get("published")):
        by_id[x["id"]] = x
ids = sorted(by_id)
meta = {}
for f in glob.glob(meta_dir + r"\*.json"):
    vid = os.path.splitext(os.path.basename(f))[0]
    meta[vid] = json.load(open(f, encoding="utf-8-sig"))
extra = [k for k in meta if k not in by_id]
print(f"cands rows={len(cands)} unique={len(by_id)} meta={len(meta)} extra={extra}")
rows=[]
for vid in ids:
    c = by_id[vid]
    m = meta.get(vid, {})
    ch = cons.get(c["slug"], {})
    title = m.get("title") or c.get("title") or ""
    chapters = m.get("chapters") or []
    desc = m.get("shortDescription") or ""
    # derive robust observable features
    title_words = re.findall(r"[A-Za-z0-9']+", title)
    desc_words = re.findall(r"[A-Za-z0-9']+", desc)
    n_ch = len(chapters)
    chap_titles = " | ".join((x.get("title") or "") for x in chapters[:20])
    # chapter density per minute; duration from meta
    dur = None
    try: dur = float(m.get("lengthSeconds")) if m.get("lengthSeconds") else c.get("length_s")
    except Exception: dur = None
    dens = (n_ch / (dur/60) if dur else None)
    # opening evidence: first chapter and first 500 chars of description (not script)
    first_ch = chapters[0].get("title") if chapters else "UNKNOWN"
    groups = [t.lower() for t in title_words]
    has_q = "?" in title
    has_how = "how" in groups
    has_why = "why" in groups
    has_what = "what" in groups
    story_patterns = []
    if has_q: story_patterns.append("question")
    if has_how: story_patterns.append("How")
    if has_why: story_patterns.append("Why")
    if has_what: story_patterns.append("What")
    mwords = [w for w in groups if w in {"the","a","an","of","in","to"}]
    rows.append({
      "video_id": vid,
      "url": c.get("url") or f"https://www.youtube.com/watch?v={vid}",
      "channel_slug": c["slug"],
      "channel_title": ch.get("title","UNKNOWN"),
      "selection_kind": c.get("kind",""),
      "grid_title": c.get("title",""),
      "verified_title": title,
      "publish_date": m.get("publishDate","UNKNOWN"),
      "author": m.get("author","UNKNOWN"),
      "category": m.get("category","UNKNOWN"),
      "views_exact": m.get("viewCount","UNKNOWN"),
      "duration_s": int(dur) if dur else "UNKNOWN",
      "duration_min": round(dur/60,2) if dur else "UNKNOWN",
      "chapter_count": n_ch,
      "chapter_titles_20": chap_titles,
      "chapter_density_per_min": round(dens,2) if dens is not None else "UNKNOWN",
      "first_chapter": first_ch or "UNKNOWN",
      "description_chars": len(desc),
      "description_words": len(desc_words),
      "description_first_500": desc[:500].replace("\n"," "),
      "title_question": has_q,
      "title_How_Why_What": ";".join(story_patterns) if story_patterns else "none",
      "title_word_count": len(title_words),
      "title_chars": len(title),
      "hook_analysis": "OBSERVED (proxy): title question/pattern + first chapter + description opening. Not transcript or retention.",
      "script_structure_evidence": f"chapter_count={n_ch}; first={first_ch}; chapters={chap_titles[:600]}",
      "visual_production_analysis": "UNKNOWN: metadata does not reveal visuals/AI use. Requires frame/transcript inspection.",
      "editing_pacing_proxy": f"chapter_density={dens}; duration={dur}s; visual-change frequency not in metadata.",
      "engagement_hypothesis": "HYPOTHESIS: question/title + chapter labels may support curiosity; not proven by views.",
      "evidence_trust": "VERIFIED metadata where present; analysis labelled proxy/hypothesis/unknown.",
      "access_date": "2026-11-05",
    })
# add extra channel if absent
for vid in extra:
    m=meta[vid]; chapters=m.get("chapters") or []; desc=m.get("shortDescription") or ""; dur=None
    try: dur=float(m.get("lengthSeconds"))
    except Exception: pass
    title=m.get("title") or ""
    rows.append({"video_id":vid,"url":f"https://www.youtube.com/watch?v={vid}","channel_slug":"alt-shift-x","channel_title":"Alt Shift X","selection_kind":"extra_verified_capture","grid_title":title,"verified_title":title,"publish_date":m.get("publishDate","UNKNOWN"),"author":m.get("author","UNKNOWN"),"category":m.get("category","UNKNOWN"),"views_exact":m.get("viewCount","UNKNOWN"),"duration_s":int(dur) if dur else "UNKNOWN","duration_min":round(dur/60,2) if dur else "UNKNOWN","chapter_count":len(chapters),"chapter_titles_20":" | ".join((x.get("title") or "") for x in chapters[:20]),"chapter_density_per_min":round(len(chapters)/(dur/60),2) if dur else "UNKNOWN","first_chapter":chapters[0].get("title") if chapters else "UNKNOWN","description_chars":len(desc),"description_words":len(re.findall(r"[A-Za-z0-9']+",desc)),"description_first_500":desc[:500].replace("\n"," "),"title_question":"?" in title,"title_How_Why_What":"none","title_word_count":len(re.findall(r"[A-Za-z0-9']+",title)),"title_chars":len(title),"hook_analysis":"OBSERVED (proxy)","script_structure_evidence":f"chapter_count={len(chapters)}","visual_production_analysis":"UNKNOWN","editing_pacing_proxy":"UNKNOWN beyond chapter density","engagement_hypothesis":"HYPOTHESIS only","evidence_trust":"VERIFIED metadata","access_date":"2026-11-05"})
rows.sort(key=lambda r: (r["channel_slug"], r["video_id"]))
json.dump(rows, open(root+r"\data\video_analysis.json","w",encoding="utf-8"), indent=2, ensure_ascii=False)
with open(root+r"\data\video_analysis.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(f"wrote {len(rows)} rows; channels={len({r['channel_slug'] for r in rows})} unique_ids={len({r['video_id'] for r in rows})}")
# summary stats for report
print("question_titles", sum(r["title_question"] for r in rows))
print("median_dur", statistics.median([r["duration_s"] for r in rows if r["duration_s"]!="UNKNOWN"]))
print("median_chapters", statistics.median([r["chapter_count"] for r in rows]))
print("median_density", statistics.median([r["chapter_density_per_min"] for r in rows if r["chapter_density_per_min"]!="UNKNOWN"]))
print("with_chapters", sum(r["chapter_count"]>0 for r in rows))
print(json.dumps({"rows":len(rows),"unique":len({r['video_id'] for r in rows}),"channels":len({r['channel_slug'] for r in rows})}))

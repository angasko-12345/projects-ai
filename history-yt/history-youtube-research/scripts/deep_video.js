// Watch-page deep extractor: description, chapters, counts, dates, likes
(() => {
  const d = window.ytInitialPlayerResponse; if (!d) return "NODATA";
  const out = {};
  try {
    const mf = d.microformat && d.microformat.playerMicroformatRenderer;
    out.title = mf && mf.title && mf.title.simpleText;
    out.publishDate = mf && mf.publishDate;
    out.category = mf && mf.category;
  } catch(e){}
  try {
    const vd = d.videoDetails;
    out.videoId = vd && vd.videoId;
    out.author = vd && vd.author;
    out.lengthSeconds = vd && vd.lengthSeconds;
    out.viewCount = vd && vd.viewCount;
    out.shortDescription = vd && vd.shortDescription;
  } catch(e){}
  const chapters = [];
  try {
    const init = window.ytInitialData;
    const walk = (n) => {
      if (!n || typeof n !== "object") return;
      if (n.macroMarkersListItemRenderer) {
        const c = n.macroMarkersListItemRenderer;
        const t = c.title && (c.title.simpleText || (c.title.runs||[]).map(r=>r.text).join(""));
        chapters.push({ time_ms: Number(c.timeRangeStartMillis||0), title: t });
      }
      Object.values(n).forEach(walk);
    };
    walk(init);
  } catch(e){}
  out.chapters = chapters;
  // dedupe chapters (YouTube duplicates entries)
  const seen = new Set(); out.chapters = chapters.filter(c => { const k = c.title + "@" + Math.floor(c.time_ms/1000); if (seen.has(k)) return false; seen.add(k); return true; });
  return JSON.stringify(out);
})()

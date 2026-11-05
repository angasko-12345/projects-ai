// Watch page extractor: description, chapters, counts, publish date
(() => {
  const d = window.ytInitialPlayerResponse; if (!d) return "NODATA";
  const out = {};
  try {
    const mf = d.microformat && d.microformat.playerMicroformatRenderer;
    out.title = mf && mf.title && mf.title.simpleText;
    out.publishDate = mf && mf.publishDate;
    out.uploadDate = mf && mf.uploadDate;
    out.category = mf && mf.category;
    out.isLive = mf && mf.liveBroadcastDetails;
  } catch(e){}
  try {
    const vd = d.videoDetails;
    out.videoId = vd && vd.videoId;
    out.author = vd && vd.author;
    out.lengthSeconds = vd && vd.lengthSeconds;
    out.viewCount = vd && vd.viewCount;
    out.shortDescription = vd && vd.shortDescription;
  } catch(e){}
  // chapters from ytInitialData: find "macroMarkersListItemRenderer"
  const chapters = [];
  try {
    const init = window.ytInitialData;
    const walk = (n) => {
      if (!n || typeof n !== "object") return;
      if (n.macroMarkersListItemRenderer) {
        const c = n.macroMarkersListItemRenderer;
        const t = c.title && (c.title.simpleText || (c.title.runs||[]).map(r=>r.text).join(""));
        const ms = c.timeRangeStartMillis;
        chapters.push({ time_ms: Number(ms||0), title: t });
      }
      Object.values(n).forEach(walk);
    };
    walk(init);
  } catch(e){}
  out.chapters = chapters;
  // engagement (likes/comments shown only if present in DOM)
  try {
    out.likeButtonText = (document.querySelector("like-button-view-model button")||{}).getAttribute && document.querySelector("like-button-view-model button").getAttribute("aria-label");
  } catch(e){}
  return JSON.stringify(out);
})()

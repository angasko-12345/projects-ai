// Recursive walker: collects video-like nodes from a page's ytInitialData.
// Returns compact JSON array of {id,title,views,published,length,url}
(() => {
  const d = window.ytInitialData; if (!d) return "NODATA";
  const videos = [];
  const seen = new Set();
  const txt = (o) => {
    if (!o) return undefined;
    if (typeof o === "string") return o;
    if (o.simpleText) return o.simpleText;
    if (Array.isArray(o.runs)) return o.runs.map(r => r.text).join("");
    if (o.content) return o.content;
    if (o.accessibilityText) return o.accessibilityText;
    return undefined;
  };
  const push = (v) => {
    if (!v || !v.id || seen.has(v.id)) return;
    seen.add(v.id); videos.push(v);
  };
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.videoRenderer) {
      const v = node.videoRenderer;
      push({ id: v.videoId, title: txt(v.title), views: txt(v.viewCountText) || txt(v.shortViewCountText),
        published: txt(v.publishedTimeText), length: txt(v.lengthText),
        url: "https://www.youtube.com/watch?v=" + v.videoId });
    }
    if (node.lockupViewModel) {
      const v = node.lockupViewModel;
      const md = (v.metadata && (v.metadata.lockupMetadataViewModel || v.metadata.contentMetadataViewModel)) || {};
      const rows = md.metadata && md.metadata.contentMetadataViewModel && md.metadata.contentMetadataViewModel.metadataRows || md.contentMetadataViewModel && md.contentMetadataViewModel.metadataRows || [];
      const parts = [];
      (rows || []).forEach(r => (r.metadataParts || []).forEach(p => { const t = txt(p.text); if (t) parts.push(t); }));
      // duration: look in contentImage for badge
      let dur;
      try { const b = v.contentImage && v.contentImage.thumbnailViewModel && v.contentImage.thumbnailViewModel.overlays; JSON.stringify(b).match(/\d+:\d+/g); } catch(e){}
      try { dur = (JSON.stringify(v).match(/"[0-9]+:[0-9]{2}"/g) || [])[0]; if(dur) dur = dur.slice(1,-1); } catch(e){}
      push({ id: v.contentId, title: md.title ? txt(md.title) : undefined, views: parts.find(p => /view/i.test(p)),
        published: parts.find(p => /ago|year|month|week|day/i.test(p)), length: dur, parts: parts.join(" | "),
        url: "https://www.youtube.com/watch?v=" + v.contentId });
    }
    if (Array.isArray(node)) { node.forEach(walk); return; }
    Object.values(node).forEach(walk);
  };
  walk(d);
  // channel meta
  let channel = {};
  try { const meta = d.metadata && d.metadata.channelMetadataRenderer; channel = { title: meta && meta.title, id: meta && meta.externalId }; } catch(e){}
  let headerText;
  try { headerText = (document.querySelector("yt-page-header-renderer")||{}).innerText; } catch(e){}
  return JSON.stringify({ channel, headerText, count: videos.length, videos });
})()

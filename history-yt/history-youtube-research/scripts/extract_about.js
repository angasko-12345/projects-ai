// Returns body innerText trimmed to 1500 chars (for /about tab: joined date, views)
(() => (document.body && document.body.innerText ? document.body.innerText.replace(/\n{2,}/g,"\n").slice(0,1500) : "NODATA"))()

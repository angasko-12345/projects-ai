import json
import sys

f = sys.argv[1] if len(sys.argv) > 1 else r"D:\admin\code\projects\history-yt\history-youtube-research\data\video_meta\MvwAWgiERBM.json"
d = json.load(open(f, encoding="utf-8-sig"))
print(d.get("title"))
print("views:", d.get("viewCount"), "len_s:", d.get("lengthSeconds"), "date:", d.get("publishDate"))
print("desc chars:", len(d.get("shortDescription") or ""))
print("chapters:", len(d.get("chapters") or []))
for c in (d.get("chapters") or [])[:8]:
    print(" ", c.get("time_ms"), c.get("title"))

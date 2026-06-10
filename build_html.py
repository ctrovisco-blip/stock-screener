"""
Builds index.html by injecting screener JSON data into the template.
Reads:  screener-template.html + data/screener.json
Writes: index.html
"""
import json, os, sys
from datetime import datetime, timezone

print("Building index.html...")

with open("screener-template.html", encoding="utf-8") as f:
    html = f.read()

screener_data = {}
if os.path.exists("data/screener.json"):
    with open("data/screener.json", encoding="utf-8") as f:
        screener_data = json.load(f)
    print(f"  Screener data: OK ({len(screener_data)} tickers)")
else:
    print("  Screener data: not found, using {}")

def file_mtime(path):
    if not os.path.exists(path):
        return "n/a"
    t = os.path.getmtime(path)
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

screener_js   = json.dumps(screener_data, separators=(',',':'), ensure_ascii=False)
updated       = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
fetched_at    = file_mtime("data/screener.json")

html = html.replace("/*__SCREENER_DATA__*/", screener_js)
html = html.replace("__UPDATED__",           updated)
html = html.replace("__FETCHED_AT__",        fetched_at)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nindex.html written ({len(html)//1024}KB)")
print(f"  Tickers: {len(screener_data)}")
print(f"  Build timestamp: {updated}")

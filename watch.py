#!/usr/bin/env python3
import argparse
import html
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "https://www.vhsit.berlin.de/vhskurse/BusinessPages/"
SEARCH_URL = BASE + "CourseSearch.aspx?" + urllib.parse.urlencode(
    {"direkt": 1, "begonnen": 0, "beendet": 0, "stichw": "Sprachtest zur Einbürgerung"},
    encoding="cp1252",
)
STATE_FILE = Path(os.environ.get("WATCH_STATE_FILE", Path(__file__).with_name("state.json")))
FULL = "belegt"


def make_opener():
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (personal VHS Sprachtest watcher)")]
    return opener


def fetch(opener, url, form=None):
    data = urllib.parse.urlencode(form, encoding="cp1252").encode() if form else None
    with opener.open(url, data, timeout=30) as resp:
        return resp.geturl(), resp.read().decode("cp1252", errors="replace")


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_rows(page):
    courses = []
    for row in re.findall(r'<tr class="DataGrid(?:Alternating)?Item">(.*?)</tr>', page, re.S):
        cell = lambda cls: text(m.group(1)) if (m := re.search(rf'class="{cls}"[^>]*>(.*?)</td>', row, re.S)) else ""
        link = re.search(r'href="CourseDetail\.aspx\?id=(\d+)"', row)
        if not link:
            continue
        courses.append({
            "id": link.group(1),
            "district": cell("DataGridItemDistrict"),
            "number": cell("DataGridItemCourseNumber"),
            "begin": cell("DataGridItemCourseBegin"),
            "places": cell("DataGridItemPlaces"),
            "note": cell("DataGridItemBooking"),
            "url": BASE + "CourseDetail.aspx?id=" + link.group(1),
        })
    return courses


def postback_form(page, button_name):
    form = {
        html.unescape(name): html.unescape(value)
        for name, value in re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page)
    }
    for select_name, body in re.findall(r'<select name="([^"]+)".*?>(.*?)</select>', page, re.S):
        if selected := re.search(r'<option selected="selected" value="([^"]*)"', body):
            form[html.unescape(select_name)] = html.unescape(selected.group(1))
    form[button_name + ".x"] = "5"
    form[button_name + ".y"] = "5"
    return form


def fetch_all_courses():
    opener = make_opener()
    url, page = fetch(opener, SEARCH_URL)
    courses = parse_rows(page)
    for _ in range(20):
        pager = re.search(r"Seite (\d+) von (\d+)", page)
        if not pager or int(pager.group(1)) >= int(pager.group(2)):
            break
        next_button = re.search(r'<input type="image" name="([^"]+)" class="DataGridPager" src="[^"]*arrow_right\.gif"', page)
        if not next_button:
            break
        time.sleep(2)
        url, page = fetch(opener, url, postback_form(page, next_button.group(1)))
        courses += parse_rows(page)
    if not courses and "Anzahl Treffer" not in page:
        raise RuntimeError("Unexpected page layout; the VHS site may have changed.")
    return {c["id"]: c for c in courses}


def notify(title, message, ntfy_topic, click_url=SEARCH_URL):
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {title}\n{message}\n", flush=True)
    if sys.platform == "darwin":
        script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\nend run'
        subprocess.run(["osascript", "-e", script, title, message[:200]], check=False)
    if ntfy_topic:
        req = urllib.request.Request(
            "https://ntfy.sh/" + ntfy_topic,
            data=message.encode(),
            headers={"Title": title, "Priority": "high", "Click": click_url},
        )
        try:
            urllib.request.urlopen(req, timeout=15)
        except OSError as err:
            print(f"ntfy failed: {err}", file=sys.stderr)


def describe(c):
    return f"{c['begin']} · {c['district']} · {c['places']} · {c['note']}\n{c['url']}"


def check(ntfy_topic, verbose):
    courses = fetch_all_courses()
    previous = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    STATE_FILE.write_text(json.dumps(courses, ensure_ascii=False, indent=1))

    if verbose or previous is None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] {len(courses)} dates found:")
        for c in sorted(courses.values(), key=lambda c: datetime.strptime(c["begin"][:10], "%d.%m.%Y")):
            print("  " + describe(c).replace("\n", "\n    "))
        print(flush=True)
    if previous is None:
        return

    newly_free = [c for id_, c in courses.items() if c["places"] != FULL and previous.get(id_, {}).get("places", FULL) == FULL]
    newly_listed = [c for id_, c in courses.items() if id_ not in previous]

    if newly_free:
        notify("VHS Sprachtest: free places!", "\n\n".join(map(describe, newly_free)), ntfy_topic, newly_free[0]["url"])
        if sys.platform == "darwin":
            for c in newly_free[:3]:
                subprocess.run(["open", c["url"]], check=False)
    if newly_listed:
        notify("VHS Sprachtest: new dates listed", "\n\n".join(map(describe, newly_listed)), ntfy_topic)


def main():
    parser = argparse.ArgumentParser(description="Watch Berlin VHS 'Sprachtest zur Einbürgerung' dates.")
    parser.add_argument("--interval", type=int, default=10, help="minutes between checks (default 10)")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--ntfy", metavar="TOPIC", help="also push to your phone via ntfy.sh/TOPIC")
    parser.add_argument("--verbose", action="store_true", help="print the full list on every check")
    args = parser.parse_args()

    while True:
        try:
            check(args.ntfy, args.verbose or args.once)
        except Exception as err:
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] check failed: {err}", file=sys.stderr, flush=True)
        if args.once:
            break
        time.sleep(max(args.interval, 5) * 60)


if __name__ == "__main__":
    main()

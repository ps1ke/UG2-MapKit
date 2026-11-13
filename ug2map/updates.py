"""Update checker: asks GitHub (once a day, in the background) whether a newer version of UG2 MapKit exists. It only NOTIFIES; nothing is downloaded or installed.
Opt out with --no-update-check. The request carries no information about you or your files, only a plain GitHub raw-file download."""
import json, os, re, time, urllib.request
from . import __version__

REPO = "ps1ke/UG2-MapKit"
RAW = f"https://api.github.com/repos/{REPO}/contents/ug2map/__init__.py?ref=main"          # the API is always fresh; raw.githubusercontent.com is cached for minutes
PAGE = f"https://github.com/{REPO}"
EVERY = 24 * 3600

def _ver(v): return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) + (0,) * (4 - len(re.findall(r"\d+", v)[:4]))

def fetch_latest(timeout=4):
    req = urllib.request.Request(RAW, headers={"User-Agent": "UG2-MapKit-update-check", "Accept": "application/vnd.github.raw+json"})
    m = re.search(r'__version__\s*=\s*"([^"]+)"', urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
    return m.group(1) if m else None

def check(cache_file=None, fetch=fetch_latest, now=None):
    """returns {current, latest, newer, url, checked}; uses the cache file when it is younger than a day; never raises"""
    now = now or time.time(); res = {"current": __version__, "latest": None, "newer": False, "url": PAGE, "checked": None}
    try:
        if cache_file and os.path.exists(cache_file):
            c = json.load(open(cache_file))
            if now - c.get("checked", 0) < EVERY and c.get("current") == __version__: return c
        latest = fetch()
        if latest: res.update(latest=latest, newer=_ver(latest) > _ver(__version__), checked=now)
        if cache_file and res["latest"]: json.dump(res, open(cache_file, "w"))
    except Exception: pass                                   # offline / blocked / rate-limited: stay silent
    return res

"""
Find the careers page / ATS board behind a company website and check whether it has Israeli jobs.

Used by watchlist.py to verify research-seed employers before they are crawled. Network-bound;
every request has a timeout and failures return 'unknown' rather than 'closed'.
"""

import re
from urllib.parse import urljoin, urlparse

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
ISRAEL_RE = re.compile(r"israel|tel[\s\-]*aviv|herzliya|haifa|jerusalem|ramat[\s\-]*gan|petah|petach|ra'?anana|netanya|"
                       r"yokneam|rehovot|holon|kfar[\s\-]*saba|hod hasharon|rosh ha'?ayin|caesarea|or yehuda|\bIL\b", re.I)

ATS_LINK_PATTERNS = [
    ("comeet", re.compile(r"https?://(?:www\.)?comeet\.(?:com|co)/jobs/([\w\-]+)/([0-9A-F]{2}\.[0-9A-F]{3})", re.I)),
    ("greenhouse", re.compile(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w\-]+)", re.I)),
    ("lever", re.compile(r"https?://jobs(\.eu)?\.lever\.co/([\w\-]+)", re.I)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([\w\-\.]+)", re.I)),
    ("workday", re.compile(r"https?://([\w\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w\-]+)", re.I)),
    ("smartrecruiters", re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w\-]+)", re.I)),
]
COMEET_UID = re.compile(r"(?:company-uid=|comeet_uid[\"']?\s*[:=]\s*)[\"']([0-9A-F]{2}\.[0-9A-F]{3})", re.I)
COMEET_TOKEN = re.compile(r"(?:comeet-token=|comeet_token[\"']?\s*[:=]\s*|token=)[\"']([0-9A-F]{20,})", re.I)
BAD_SLUGS = {"embed", "v1", "js", "jobs", "api", "assets", "static", "wday", "en-us", "job_board"}
STATIC_ASSET_RE = re.compile(r"/wp-content/|/wp-includes/|\.(?:css|js|json|png|jpe?g|svg|gif|webp|ico|woff2?|ttf|pdf|xml)(?:\?|$)", re.I)
CAREER_LINK_RE = re.compile(r"career|jobs|positions|join[\-_ ]?us|open[\-_ ]roles|we-?re-?hiring|משרות|דרושים|קריירה", re.I)


def _get(url, timeout=12):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None


def _israel_count(locations):
    return sum(1 for loc in locations if ISRAEL_RE.search(loc or ""))


def check_board(ats_url):
    """
    Return {"ok": bool, "jobs": int, "israel_jobs": int|None} for a board URL produced by discover().
    israel_jobs None = could not determine (e.g. Workday needs a POST per page; checked at crawl time).
    """
    if ats_url.startswith("comeet-api:"):
        _, uid, token = ats_url.split(":", 2)
        r = _get(f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}")
        if r is None or not r.ok:
            return {"ok": False, "jobs": 0, "israel_jobs": None}
        jobs = r.json()
        return {"ok": True, "jobs": len(jobs),
                "israel_jobs": _israel_count(f"{(j.get('location') or {}).get('name', '')} {(j.get('location') or {}).get('country', '')}" for j in jobs)}
    p = urlparse(ats_url)
    parts = [x for x in p.path.split("/") if x]
    host = p.netloc.lower()
    if "greenhouse.io" in host and parts:
        r = _get(f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs")
        if r is None or not r.ok:
            return {"ok": False, "jobs": 0, "israel_jobs": None}
        jobs = r.json().get("jobs", [])
        return {"ok": True, "jobs": len(jobs), "israel_jobs": _israel_count((j.get("location") or {}).get("name", "") for j in jobs)}
    if "lever.co" in host and parts:
        eu = ".eu" if host.startswith("jobs.eu.") else ""
        r = _get(f"https://api{eu}.lever.co/v0/postings/{parts[0]}?mode=json")
        if r is None or not r.ok or not isinstance(r.json(), list):
            return {"ok": False, "jobs": 0, "israel_jobs": None}
        jobs = r.json()
        return {"ok": True, "jobs": len(jobs), "israel_jobs": _israel_count((j.get("categories") or {}).get("location", "") for j in jobs)}
    if host == "jobs.ashbyhq.com" and parts:
        r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}")
        if r is None or not r.ok:
            return {"ok": False, "jobs": 0, "israel_jobs": None}
        jobs = r.json().get("jobs", [])
        return {"ok": True, "jobs": len(jobs), "israel_jobs": _israel_count(j.get("location", "") for j in jobs)}
    if "comeet." in host and len(parts) >= 3:
        r = _get(ats_url)
        ok = r is not None and r.ok and "COMPANY_POSITIONS_DATA" in r.text
        return {"ok": ok, "jobs": None, "israel_jobs": None}
    if "smartrecruiters.com" in host and parts:
        r = _get(f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings?country=il&limit=1")
        if r is None or not r.ok:
            return {"ok": False, "jobs": 0, "israel_jobs": None}
        n = r.json().get("totalFound", 0)
        return {"ok": True, "jobs": n, "israel_jobs": n}
    if host.endswith("myworkdayjobs.com"):
        return {"ok": True, "jobs": None, "israel_jobs": None}
    return {"ok": False, "jobs": 0, "israel_jobs": None}


def _comeet_widget(text):
    u, t = COMEET_UID.search(text), COMEET_TOKEN.search(text)
    if u and t:
        candidate = f"comeet-api:{u.group(1)}:{t.group(1)}"
        if check_board(candidate)["ok"]:
            return candidate
    return None


def _scan(text):
    w = _comeet_widget(text)
    if w:
        return w
    for kind, pat in ATS_LINK_PATTERNS:
        for m in pat.finditer(text):
            if kind == "comeet":
                cand = f"https://www.comeet.com/jobs/{m.group(1)}/{m.group(2).upper()}"
            elif kind == "greenhouse":
                if m.group(1).lower() in BAD_SLUGS:
                    continue
                cand = f"https://job-boards.greenhouse.io/{m.group(1)}"
            elif kind == "lever":
                cand = f"https://jobs{'.eu' if m.group(1) else ''}.lever.co/{m.group(2)}"
            elif kind == "ashby":
                cand = f"https://jobs.ashbyhq.com/{m.group(1)}"
            elif kind == "workday":
                if m.group(3).lower() in BAD_SLUGS:
                    continue
                cand = f"https://{m.group(1)}.{m.group(2)}.myworkdayjobs.com/{m.group(3)}"
            else:
                cand = f"https://jobs.smartrecruiters.com/{m.group(1)}"
            if check_board(cand)["ok"]:
                return cand
    return None


def slug_candidates(name):
    base = re.sub(r"\(.*?\)", "", name).lower()
    words = re.findall(r"[a-z0-9]+", base)
    trimmed = [w for w in words if w not in ("israel", "ltd", "inc", "technologies", "group", "the", "labs", "security",
                                              "energy", "robotics", "imaging", "semiconductor", "intelligence", "medical",
                                              "analytics", "capital", "materials", "health")]
    out = []
    for ws in (words, trimmed):
        if ws:
            out += ["".join(ws), "-".join(ws)]
    return [s for s in dict.fromkeys(out) if len(s) >= 3]


def guess_board(name):
    """Try public Greenhouse/Lever/Ashby slugs; only accept a board that lists at least one Israeli job."""
    for slug in slug_candidates(name):
        for cand in (f"https://job-boards.greenhouse.io/{slug}", f"https://jobs.lever.co/{slug}",
                     f"https://jobs.eu.lever.co/{slug}", f"https://jobs.ashbyhq.com/{slug}"):
            res = check_board(cand)
            if res["ok"] and (res["israel_jobs"] or 0) > 0:
                return cand
    return None


def discover(name, website, careers_url=None):
    """
    Returns dict: status (verified_ats | careers_page_found | no_careers_page | unreachable),
    careers_url, ats, board_check.
    """
    pages = [u for u in (careers_url, website) if u]
    homepage_ok = False
    careers_found = careers_url
    for url in pages:
        r = _get(url)
        if r is None or not r.ok:
            continue
        homepage_ok = True
        found = _scan(r.url + " " + r.text)
        if found:
            return {"status": "verified_ats", "careers_url": careers_found or r.url, "ats": found, "board_check": check_board(found)}
        # follow up to 4 careers-looking links one level deep
        links = re.findall(r'href=["\']([^"\'#]+)["\']', r.text)
        next_links = []
        for link in links:
            full = urljoin(r.url, link)
            if STATIC_ASSET_RE.search(full):
                continue
            if CAREER_LINK_RE.search(link) and urlparse(full).scheme.startswith("http"):
                next_links.append(full)
        for link in list(dict.fromkeys(next_links))[:4]:
            hit = _scan(link)
            if hit:
                return {"status": "verified_ats", "careers_url": link, "ats": hit, "board_check": check_board(hit)}
            r2 = _get(link)
            if r2 is not None and r2.ok:
                careers_found = careers_found or r2.url
                hit = _scan(r2.url + " " + r2.text)
                if hit:
                    return {"status": "verified_ats", "careers_url": r2.url, "ats": hit, "board_check": check_board(hit)}
    guessed = guess_board(name)
    if guessed:
        return {"status": "verified_ats", "careers_url": careers_found, "ats": guessed, "board_check": check_board(guessed)}
    if careers_found:
        return {"status": "careers_page_found", "careers_url": careers_found, "ats": None, "board_check": None}
    return {"status": "no_careers_page" if homepage_ok else "unreachable", "careers_url": None, "ats": None, "board_check": None}

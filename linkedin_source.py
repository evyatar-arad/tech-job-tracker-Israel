"""
LinkedIn job discovery through LinkedIn's PUBLIC guest job-search pages (no login, no account, no cookies).

Why no account: automating a logged-in personal LinkedIn session violates LinkedIn's terms far more directly
and risks restricting the user's own profile. The guest pages are what any logged-out visitor sees.
Volume is deliberately small (a few dozen requests per run, with delays) and any block (HTTP 429/999)
stops LinkedIn for the rest of the run and is reported in data/crawl_health.json.
"""

import random
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

SEARCH_URL = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
              "?keywords={kw}&location={loc}&f_E={exp}&f_TPR=r{tpr}&start={start}")
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
BLOCK_STATUSES = {429, 999, 403}


class LinkedInBlocked(Exception):
    pass


def _get(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    if r.status_code in BLOCK_STATUSES:
        raise LinkedInBlocked(f"HTTP {r.status_code}")
    return r


def canonical_job_url(job_id):
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def parse_search_results(html):
    """Return a list of dicts: job_id, title, company, location, posted (raw)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("li"):
        title = card.select_one(".base-search-card__title")
        link = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
        urn = card.select_one("[data-entity-urn]")
        job_id = None
        if urn and urn.get("data-entity-urn"):
            m = re.search(r"(\d{8,})", urn["data-entity-urn"])
            job_id = m.group(1) if m else None
        if not job_id and link and link.get("href"):
            m = re.search(r"-(\d{8,})(?:\?|$)", link["href"]) or re.search(r"/(\d{8,})", link["href"])
            job_id = m.group(1) if m else None
        if not (title and job_id):
            continue
        company = card.select_one(".base-search-card__subtitle")
        loc = card.select_one(".job-search-card__location")
        posted = card.select_one("time")
        out.append({
            "job_id": job_id,
            "title": title.get_text(" ", strip=True),
            "company": company.get_text(" ", strip=True) if company else "",
            "location": loc.get_text(" ", strip=True) if loc else "",
            "posted": posted.get("datetime", "") if posted else "",
        })
    return out


def parse_detail(html):
    """Return (description_text, criteria_text) from a guest jobPosting page."""
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    criteria = " | ".join(li.get_text(" ", strip=True) for li in soup.select(".description__job-criteria-item"))
    return (desc.get_text("\n", strip=True) if desc else ""), criteria


def search(queries, settings, is_title_candidate, location_ok, log=print):
    """
    Run the configured queries. Returns (raw_cards, stats). Cards that fail the title / location
    pre-filters are dropped here so they never reach the detail fetch or the AI.
    """
    stats = {"requests": 0, "cards": 0, "kept": 0, "blocked": None}
    seen, kept = set(), []
    delay = float(settings.get("delay_seconds", 2.0))
    for q in queries:
        for page in range(int(settings.get("pages_per_query", 2))):
            url = SEARCH_URL.format(kw=quote(q), loc=quote(settings.get("location", "Israel")),
                                    exp=quote(settings.get("experience_filter", "1,2,3")),
                                    tpr=int(settings.get("posted_within_seconds", 604800)), start=page * 25)
            try:
                r = _get(url)
                stats["requests"] += 1
            except LinkedInBlocked as e:
                stats["blocked"] = str(e)
                log(f"  [!] LinkedIn blocked the search ({e}) - stopping LinkedIn for this run.")
                return kept, stats
            except requests.RequestException as e:
                log(f"  [!] LinkedIn network error: {type(e).__name__}")
                break
            if r.status_code != 200 or not r.text.strip():
                break
            cards = parse_search_results(r.text)
            stats["cards"] += len(cards)
            for c in cards:
                if c["job_id"] in seen:
                    continue
                seen.add(c["job_id"])
                if not is_title_candidate(c["title"], canonical_job_url(c["job_id"])):
                    continue
                if not location_ok(c["company"], c["location"]):
                    continue
                kept.append(c)
            if len(cards) < 10:
                break
            time.sleep(delay + random.uniform(0, 1.0))
        time.sleep(delay + random.uniform(0, 1.0))
    stats["kept"] = len(kept)
    return kept, stats


def fetch_detail(job_id):
    """Return (description, criteria) or ('', '') on failure. Raises LinkedInBlocked on a block."""
    try:
        r = _get(DETAIL_URL.format(job_id=job_id))
    except requests.RequestException:
        return "", ""
    if r.status_code != 200:
        return "", ""
    return parse_detail(r.text)

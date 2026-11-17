import json
import os

import pytest

import linkedin_source as li
import storage
import watchlist


def test_atomic_write_never_corrupts_existing_file(tmp_path):
    p = tmp_path / "x.json"
    storage.atomic_write_json(str(p), [{"a": 1}])
    with pytest.raises(TypeError):
        storage.atomic_write_json(str(p), [{"a": object()}])  # not serializable
    assert json.loads(p.read_text(encoding="utf-8")) == [{"a": 1}]
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]


def test_archive_is_byte_exact_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ARCHIVE_DIR", str(tmp_path / "archive"))
    raw = "[\r\n  {\"a\": \"ש\"}\r\n]".encode("utf-8")
    p1, d1 = storage.archive_bytes(None, "seen", raw)
    p2, d2 = storage.archive_bytes(None, "seen", raw)
    assert p1 == p2 and d1 == d2
    assert open(p1, "rb").read() == raw


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_watchlist_sources_respect_verification_and_priority_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist, "BASE_DIR", str(tmp_path))
    _write(str(tmp_path / "data" / "company_watchlist.json"), {"companies": [
        {"company": "Wix", "company_key": "wix", "priority": "P1", "sector": "SaaS", "registry_name": "Wix"},
        {"company": "Finout", "company_key": "finout", "priority": "P1", "sector": "AI"},
        {"company": "Nofar Energy", "company_key": "nofarenergy", "priority": "P3", "sector": "Energy"},
        {"company": "Addionics", "company_key": "addionics", "priority": "P1", "sector": "Energy"},
    ]})
    _write(str(tmp_path / "data" / "company_verification_cache.json"), {"companies": {
        "finout": {"status": "verified_ats", "ats": "https://jobs.ashbyhq.com/finout", "careers_url": "https://www.finout.io/careers"},
        "nofarenergy": {"status": "careers_page_found", "careers_url": "https://www.nofar-energy.com/careers"},
        "addionics": {"status": "no_careers_page"},
    }})
    registry = [{"name": "Wix", "career_url": "https://careers.wix.com/"}]
    cfg = {"watchlist": {}, "scheduling": {"p2_hours_utc": [6, 14], "p3_hours_utc": [6]}}

    reg, extra, stats = watchlist.build_watchlist_sources(registry, cfg, hour_utc=10)
    assert reg[0]["priority"] == "P1" and "priority" not in registry[0]  # input not mutated
    assert [e["name"] for e in extra] == ["Finout"]  # P3 not at 10:00, Addionics unverified
    assert extra[0]["ats"] == "https://jobs.ashbyhq.com/finout" and extra[0]["source_type"] == "watchlist"
    assert stats["skipped_unverified"] == 1 and stats["skipped_schedule"] == 1

    _, extra6, _ = watchlist.build_watchlist_sources(registry, cfg, hour_utc=6)
    assert {e["name"] for e in extra6} == {"Finout", "Nofar Energy"}


def test_watchlist_import_keeps_companies_out_of_job_history(tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist, "WATCHLIST_FILE", str(tmp_path / "wl.json"))
    monkeypatch.setattr(storage, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    seed = tmp_path / "seed.json"
    _write(str(seed), {"companies": [{"company": "Gong", "priority": "P1", "sector": "SaaS"}]})
    out = watchlist.import_seed(str(seed), registry=[{"name": "Gong.io", "career_url": "x"}])
    assert out["do_not_append_to_seen_jobs"] is True
    assert out["companies"][0]["registry_name"] == "Gong.io"
    again = watchlist.import_seed(str(seed), registry=[{"name": "Gong.io", "career_url": "x"}])
    assert len(again["companies"]) == 1


LINKEDIN_HTML = """
<li><div class="base-card" data-entity-urn="urn:li:jobPosting:4485937694">
  <a class="base-card__full-link" href="https://il.linkedin.com/jobs/view/product-analyst-at-taboola-4485937694?position=2"></a>
  <h3 class="base-search-card__title">Product Analyst</h3>
  <h4 class="base-search-card__subtitle"><a>Taboola</a></h4>
  <span class="job-search-card__location">Tel Aviv-Yafo, Tel Aviv District, Israel</span>
  <time datetime="2026-11-15">2 days ago</time></div></li>
<li><div class="base-card"><h3 class="base-search-card__title">No id here</h3></div></li>
"""


def test_linkedin_search_parser_extracts_real_employer_and_id():
    cards = li.parse_search_results(LINKEDIN_HTML)
    assert len(cards) == 1
    c = cards[0]
    assert (c["job_id"], c["title"], c["company"]) == ("4485937694", "Product Analyst", "Taboola")
    assert "Israel" in c["location"]
    assert li.canonical_job_url(c["job_id"]) == "https://www.linkedin.com/jobs/view/4485937694"

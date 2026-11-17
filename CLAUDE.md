# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

An autonomous job crawler for Israel. It monitors ~415 registry sources (company ATS boards, career pages, AllJobs/Drushim/GotFriends searches), a research-seeded **employer watchlist** (202 companies, P1–P3), and **LinkedIn public job search**, then filters with deterministic rules + Gemini and stores real job postings in `seen_jobs.json`, shown by a static `index.html` on GitHub Pages. It runs on GitHub Actions 4x/day.

Search behaviour is defined by **profiles** in `config.json`. The active profile is `iem_product_operations` (Industrial Engineering & Management graduate: product, project/PMO, operations, business analysis, solutions/implementation, CS, BD, consulting; Gush Dan + leading companies anywhere in Israel). `software_engineering` is the original profile, preserved unchanged and selectable.

## Running Locally

```bash
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
python -m pytest -q                 # offline test suite (runs before every CI crawl)
python job_crawler.py --check       # validate config + data + Gemini key, no crawl
python job_crawler.py               # needs GEMINI_API_KEY in .env
JOB_PROFILE=software_engineering python job_crawler.py   # run another profile
CRAWL_ALL_PRIORITIES=1 python job_crawler.py              # ignore the P2/P3 schedule
```

The crawler resumes from `progress_checkpoint.json` if interrupted mid-run.

## Data files — integrity rules (read before touching them)

| File | What it is | Rules |
|---|---|---|
| `seen_jobs.json` | History of real job postings (flat JSON array) | Never shrink, never rewrite the legacy fields `company,title,location,url,experience,discovered_at,referral_contact,referral_type` — `index.html` keys the user's applied/saved state on company+title+url. Only additive fields. Non-jobs/duplicates are tagged with `status`, never deleted. The crawler refuses to save if the array would shrink. |
| `data/company_watchlist.json` | Employer candidates (NOT jobs) | Never copy companies into `seen_jobs.json`. |
| `data/company_verification_cache.json` | Live check per watchlist company: `verified_ats` / `careers_page_found` / `no_careers_page` / `unreachable` | Refreshed weekly (`verify_watchlist.yml`). Failure to fetch = unknown, not closed. |
| `data/evaluated_jobs_cache.json` | Recently rejected job_keys (per profile) | Skips re-sending the same rejected posting to Gemini for `evaluation_cache_days`. AI/network failures are NOT cached. |
| `data/crawl_health.json` | Per-source status, consecutive failures, last 60 run summaries, `alerts` (3+ failed runs) | Monitoring. |
| `data/job_updates_audit.jsonl` | Append-only log of migrations, imports, verification changes | Never rewrite. |
| `data/archive/` | Byte-exact copies taken before each migration | Rollback source. |

All writes go through `storage.atomic_write_json` (temp file → fsync → re-parse → `os.replace`).

## Job identity & dedupe (`job_identity.py`)

`job_key()`:
1. Real ATS/board posting ID → `ats:<provider>:<id>` (Greenhouse board/id, Lever/Ashby UUID, Workday `tenant/JR…`, Comeet `company:UID`, SmartRecruiters, LinkedIn, Amazon, Apple, Google, AllJobs JobID, Drushim, JobMaster, GotFriends).
2. Otherwise `fb:` hash of normalized company + cleaned title + coarse location token + canonical URL (tracking params removed).

**Never dedupe by URL alone**: shared application forms (forms.monday.com, Google Forms, Typeform, …) never yield an ID, so Moveo's three roles behind one form stay three jobs. `legacy_hash()` (company::title::url) is kept only for `index.html` compatibility.

Other helpers: `clean_title` (strips glued locations, CTA noise, "(opens in a new tab)", Hebrew "/ת" spacing), `repair_mojibake` (Latin-1-decoded Hebrew), `classify_non_job` (articles, search/category pages, logout/auth URLs, portals, company profiles), `location_is_foreign` (the JOB's title/URL/location — never the employer HQ; `(m/f/d)` counts as foreign), `parse_experience_requirement` → `explicit_student | explicit_junior | explicit_0_2 | stretch_2_plus | unspecified | senior` with evidence (silence = `unspecified`, never 0–2), `match_score` (role 35 / eligibility 25 / location 15 / employer priority 10 / skills 10 / freshness 5; weights per profile).

## Configuration (`config.json`, schema_version 2)

- `active_profile`, `profiles.<name>` — per-profile: `min_yoe`/`max_yoe`, `allowed_cities` (empty = anywhere in Israel), `location_exempt_companies`, `target_roles`, `excluded_roles`, `title_keywords(_he)`, `excluded_title_keywords(_he)`, `seniority_reject_keywords(_he)`, `title_override_keywords` (compound phrases that beat exclusions, e.g. "Sales Operations"), `linkedin_queries`, `ranking_weights`, `skills_keywords`, `description` + `ai_role_guidance` (go into the Gemini prompt).
- English keyword phrases match on word boundaries (optional plural); Hebrew phrases match as substrings — avoid short Hebrew words that are substrings of common words ("פקיד" ⊂ "תפקיד"). Do not put broad regions like "Center District" in `allowed_cities` (it would whitelist Netanya/Modiin).
- `excluded_companies` (per profile, `{group: [names]}`): employers never shown from ANY source (sources skipped, watchlist skipped, LinkedIn/job-board candidates dropped before AI). The user asked to exclude **banks and defense companies**; keep this list and the matching `excluded_title_keywords(_he)` / `excluded_roles` entries.
- Global: `gemini_model` (default `gemini-3.5-flash-lite`; the preflight falls back along `GEMINI_MODEL_PREFERENCE`, then `models.list`, if a model is retired; the key is sent in the `x-goog-api-key` header, never in the URL), `linkedin` (enabled, experience filter, posted window, pages, delays, detail-fetch cap), `watchlist` (files, which verification statuses are crawled), `scheduling` (P2/P3 hours), `evaluation_cache_days`.
- A flat (v1) config is still accepted as a single profile. Invalid JSON aborts the run.

`companies_registry_public.json` optional fields: `ats` (board URL or `comeet-api:<uid>:<token>`, beats `career_url`), `display_name` (job-board search entries), `category`. Same-board entries are scraped once.

## Pipeline (`job_crawler.py`)

0. Watchlist merge (`watchlist.build_watchlist_sources`): registry companies matched to the watchlist get `priority`/`sector`; verified watchlist-only companies become sources on the P1/P2/P3 schedule.
1. Parallel scrape (12 workers): ATS JSON APIs (Greenhouse, Lever incl. EU, Ashby, Comeet hosted + widget API, Workday — needs Origin/Referer or it 429s, SmartRecruiters, amazon.jobs) → HTML (links must look like job URLs) → Playwright fallback. 1C: LinkedIn guest search (`linkedin_source.py`, no login; sequential with delays; a 429/999 block stops LinkedIn for the run and is logged in health).
2. `select_new_candidates` (identity dedupe, refresh `last_seen_at`, skip cached rejections) → detail fetch → deterministic guards (no description, non-job, foreign location, min-YOE > max, region).
3. Gemini batches of 4: approves/rejects, returns `role_category`, `experience_level` + evidence, `role_match`, `skills_fit`. Unspecified experience is approved and labelled "לא צוין"; senior is rejected. Explicit title markers beat an AI "unspecified".
4. Atomic saves of `seen_jobs.json`, eval cache, health; Excel export.

New records = legacy fields + `role_category` + record_version-2 fields (`job_key`, `ats_provider/id`, `canonical_url`, `display_title`, `location_token`, `status`, `experience_level/evidence`, `profiles`, `first/last_seen_at`, `source_type/name`, `priority`, `sector`, `match_score`).

## Maintenance commands

```bash
python migrate_seen_jobs.py            # dry run report -> data/migration_reports/
python migrate_seen_jobs.py --apply    # archive + additive migration (idempotent)
python watchlist.py import data/seeds/company_watchlist_seed.json
python watchlist.py verify [--limit N] [--company NAME] [--max-age-days 30]
python watchlist.py report
```

Rollback of `seen_jobs.json`: copy `data/archive/seen_jobs.pre_v2.git_head.<sha>.json` (original repo bytes) over `seen_jobs.json`, or `git checkout <commit> -- seen_jobs.json`.

## GitHub Actions

- `job_crawler.yml` — UTC 06/10/14/18. Runs pytest first (crawl skipped on failure), validates JSON, crawls, commits `seen_jobs.json` + `data/crawl_health.json` + `data/evaluated_jobs_cache.json` with `[skip ci]`. `concurrency: job-crawler` prevents overlapping writers. Manual run input `crawl_all_priorities`.
- `verify_watchlist.yml` — Sundays; re-verifies watchlist companies older than 30 days.
- Requires the `GEMINI_API_KEY` secret and **Settings → Actions → Workflow permissions → Read and write**.

## Frontend (`index.html`)

Static page; loads `seen_jobs.json` and `config.json` (for `active_profile`). Defaults: active records of the active profile, sorted by `match_score`; filters for role category, experience level ("לא צוין" etc.), profile, and a toggle to show duplicates/quarantined. Shows `display_title` but keys user state on the legacy company+title+url hash. User state lives in `localStorage` only.

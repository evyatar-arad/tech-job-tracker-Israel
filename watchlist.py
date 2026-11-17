"""
Employer watchlist: research-seeded companies that are candidates for discovery, NOT job records.

Files:
  data/company_watchlist.json            - imported seed (company, sector, priority P1-P3, target titles, evidence)
  data/company_verification_cache.json   - live verification per company (careers page / ATS board / Israeli jobs)

Pipeline integration (job_crawler.py calls build_watchlist_sources()):
  * Watchlist companies already in companies_registry_public.json -> the registry entry gets priority/sector tags.
  * Other watchlist companies are crawled only once verified (status in config watchlist.crawl_statuses),
    on a P1/P2/P3 schedule.
  * Nothing here ever writes to seen_jobs.json.

CLI:
  python watchlist.py import <company_watchlist_seed.json>
  python watchlist.py verify [--limit N] [--max-age-days 30] [--all]
  python watchlist.py report
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import job_identity as ji
import storage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "data", "company_watchlist.json")
CACHE_FILE = os.path.join(BASE_DIR, "data", "company_verification_cache.json")
REGISTRY_FILE = os.path.join(BASE_DIR, "companies_registry_public.json")


def _registry_index(registry):
    idx = {}
    for c in registry:
        key = ji.normalize_company(c.get("display_name") or c["name"])
        idx.setdefault(key, c["name"])
        idx.setdefault(ji.normalize_company(c["name"]), c["name"])
    return idx


def import_seed(seed_path, registry=None):
    seed = storage.load_json(seed_path, None)
    if not seed or "companies" not in seed:
        raise SystemExit(f"{seed_path} does not look like a watchlist seed (missing 'companies').")
    registry = registry if registry is not None else storage.load_json(REGISTRY_FILE, [])
    reg_idx = _registry_index(registry)
    existing = storage.load_json(WATCHLIST_FILE, {"companies": []})
    by_key = {ji.normalize_company(c["company"]): c for c in existing.get("companies", [])}
    added = updated = 0
    for c in seed["companies"]:
        key = ji.normalize_company(c["company"])
        entry = dict(c)
        entry["company_key"] = key
        entry["registry_name"] = reg_idx.get(key)
        if key in by_key:
            if by_key[key] != entry:
                updated += 1
        else:
            added += 1
        by_key[key] = entry
    out = {
        "schema_version": "1.0.0",
        "intent": "COMPANY_DISCOVERY_ONLY_NOT_JOB_HISTORY",
        "do_not_append_to_seen_jobs": True,
        "source": os.path.basename(seed_path),
        "seed_created_at": seed.get("created_at"),
        "imported_at": storage.now_iso(),
        "data_quality_notice": seed.get("data_quality_notice", ""),
        "companies": sorted(by_key.values(), key=lambda x: (x.get("priority", "P9"), x["company"].lower())),
    }
    storage.atomic_write_json(WATCHLIST_FILE, out)
    storage.append_audit("watchlist_import", source=os.path.basename(seed_path), added=added, updated=updated,
                         total=len(out["companies"]))
    matched = sum(1 for c in out["companies"] if c["registry_name"])
    print(f"[watchlist] imported: {len(out['companies'])} companies ({added} new, {updated} updated); "
          f"{matched} already in the registry, {len(out['companies']) - matched} need verification.")
    return out


def _is_stale(entry, max_age_days):
    if not entry or not entry.get("checked_at"):
        return True
    try:
        checked = datetime.fromisoformat(entry["checked_at"])
    except ValueError:
        return True
    return datetime.now(timezone.utc) - checked > timedelta(days=max_age_days)


def verify(limit=None, max_age_days=30, include_registry_matched=False, only_company=None):
    import ats_discovery  # network-bound; imported lazily so tests of this module stay offline
    wl = storage.load_json(WATCHLIST_FILE, None)
    if not wl:
        raise SystemExit("No data/company_watchlist.json - run `python watchlist.py import <seed>` first.")
    cache = storage.load_json(CACHE_FILE, {"schema_version": "1.0.0", "companies": {}})
    if only_company:
        todo = [c for c in wl["companies"] if c["company"].lower() == only_company.lower()]
    else:
        todo = [c for c in wl["companies"]
                if (include_registry_matched or not c.get("registry_name"))
                and _is_stale(cache["companies"].get(c["company_key"]), max_age_days)]
    todo.sort(key=lambda c: c.get("priority", "P9"))
    if limit:
        todo = todo[:limit]
    print(f"[watchlist] verifying {len(todo)} companies...")

    def run(c):
        try:
            res = ats_discovery.discover(c["company"], c.get("company_website_candidate"), c.get("verified_careers_url"))
        except Exception as exc:  # never let one site kill the batch
            res = {"status": "unreachable", "careers_url": None, "ats": None, "board_check": None, "error": str(exc)[:200]}
        res["checked_at"] = storage.now_iso()
        res["company"] = c["company"]
        return c["company_key"], res

    with ThreadPoolExecutor(8) as ex:
        for key, res in ex.map(run, todo):
            prev = cache["companies"].get(key, {})
            if prev.get("status") != res["status"]:
                storage.append_audit("watchlist_verification", company=res["company"], old=prev.get("status"), new=res["status"])
            cache["companies"][key] = res
    cache["updated_at"] = storage.now_iso()
    storage.atomic_write_json(CACHE_FILE, cache)
    report(wl, cache)
    return cache


def report(wl=None, cache=None):
    wl = wl or storage.load_json(WATCHLIST_FILE, {"companies": []})
    cache = cache or storage.load_json(CACHE_FILE, {"companies": {}})
    counts = {}
    for c in wl["companies"]:
        if c.get("registry_name"):
            st = "in_registry"
        else:
            st = (cache["companies"].get(c["company_key"]) or {}).get("status", "not_checked")
        counts[st] = counts.get(st, 0) + 1
    print("[watchlist] status:", json.dumps(counts, ensure_ascii=False))
    return counts


def scheduled_now(priority, hour_utc, scheduling):
    """P1 every run; P2 on scheduling.p2_hours_utc; P3 on scheduling.p3_hours_utc; no priority -> every run."""
    if priority in (None, "", "P1"):
        return True
    if priority == "P2":
        return hour_utc in scheduling.get("p2_hours_utc", [6, 14])
    if priority == "P3":
        return hour_utc in scheduling.get("p3_hours_utc", [6])
    return True


def build_watchlist_sources(registry, global_cfg, hour_utc=None, force_all=False, exclude_fn=None):
    """
    Returns (registry_annotated, extra_sources, stats).
    registry entries matched to the watchlist get 'priority' and 'sector'; verified watchlist-only companies
    become extra sources (scheduled by priority).
    """
    wl_cfg = global_cfg.get("watchlist", {})
    wl = storage.load_json(os.path.join(BASE_DIR, wl_cfg.get("file", "data/company_watchlist.json")), None)
    if not wl:
        return registry, [], {"watchlist": 0}
    cache = storage.load_json(os.path.join(BASE_DIR, wl_cfg.get("verification_cache", "data/company_verification_cache.json")),
                              {"companies": {}})
    crawl_statuses = set(wl_cfg.get("crawl_statuses", ["verified_ats", "careers_page_found"]))
    scheduling = global_cfg.get("scheduling", {})
    hour = datetime.now(timezone.utc).hour if hour_utc is None else hour_utc

    reg_by_name = {c["name"]: c for c in registry}
    reg_idx = _registry_index(registry)
    annotated = [dict(c) for c in registry]
    ann_by_name = {c["name"]: c for c in annotated}
    extra, stats = [], {"watchlist": len(wl["companies"]), "tagged_registry": 0, "extra_sources": 0,
                        "skipped_unverified": 0, "skipped_schedule": 0, "skipped_excluded": 0}
    for c in wl["companies"]:
        if exclude_fn and exclude_fn(c["company"]):
            stats["skipped_excluded"] += 1
            continue
        reg_name = c.get("registry_name") or reg_idx.get(c["company_key"])
        if reg_name and reg_name in reg_by_name:
            ann_by_name[reg_name]["priority"] = c.get("priority")
            ann_by_name[reg_name]["sector"] = c.get("sector")
            stats["tagged_registry"] += 1
            continue
        ver = cache["companies"].get(c["company_key"]) or {}
        if ver.get("status") not in crawl_statuses:
            stats["skipped_unverified"] += 1
            continue
        if not force_all and not scheduled_now(c.get("priority"), hour, scheduling):
            stats["skipped_schedule"] += 1
            continue
        src = {
            "name": c["company"],
            "career_url": ver.get("careers_url") or c.get("company_website_candidate") or "",
            "category": c.get("sector", ""),
            "priority": c.get("priority"),
            "sector": c.get("sector"),
            "source_type": "watchlist",
        }
        if ver.get("ats"):
            src["ats"] = ver["ats"]
        extra.append(src)
    stats["extra_sources"] = len(extra)
    return annotated, extra, stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("import")
    pi.add_argument("seed")
    pv = sub.add_parser("verify")
    pv.add_argument("--limit", type=int)
    pv.add_argument("--max-age-days", type=int, default=30)
    pv.add_argument("--all", action="store_true", help="also verify companies already in the registry")
    pv.add_argument("--company", help="re-verify one company now, regardless of age")
    sub.add_parser("report")
    a = p.parse_args(argv)
    if a.cmd == "import":
        import_seed(a.seed)
    elif a.cmd == "verify":
        verify(a.limit, a.max_age_days, a.all, a.company)
    else:
        report()


if __name__ == "__main__":
    sys.exit(main())

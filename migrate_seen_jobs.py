"""
Non-destructive, idempotent migration of seen_jobs.json to record_version 2.

Guarantees:
  * The root stays a flat JSON array (index.html and the crawler keep working).
  * Every record is kept. The legacy fields (company, title, location, url, experience, discovered_at,
    referral_contact, referral_type, and role_category when present) are NEVER modified - index.html keys
    the user's applied/saved/removed state on company+title+url.
  * Only additive fields are written: job_key, ats_provider, ats_job_id, canonical_url, display_title,
    location_token, status (active|duplicate|quarantined), status_reasons, duplicate_of, experience_level,
    experience_evidence, profiles, first_seen_at, last_seen_at, quality_flags, source_type, record_version.
  * --apply first archives the exact original bytes under data/archive/ and logs to data/job_updates_audit.jsonl.
  * Running it twice produces byte-identical output.

Usage:
  python migrate_seen_jobs.py            # dry run: prints + writes data/migration_reports/<date>.json
  python migrate_seen_jobs.py --apply    # archive + atomic write
Rollback:
  copy data/archive/seen_jobs.pre_v2.<sha>.json over seen_jobs.json   (or: git checkout <commit> -- seen_jobs.json)
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

import job_identity as ji
import storage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(BASE_DIR, "seen_jobs.json")
REPORT_DIR = os.path.join(BASE_DIR, "data", "migration_reports")

LEGACY_FIELDS = ["company", "title", "location", "url", "experience", "discovered_at", "referral_contact", "referral_type"]
ADDED_FIELDS = ["record_version", "job_key", "ats_provider", "ats_job_id", "canonical_url", "display_title", "location_token",
                "status", "status_reasons", "duplicate_of", "experience_level", "experience_evidence", "profiles",
                "first_seen_at", "last_seen_at", "quality_flags", "source_type"]


def _profile_filters():
    """Title filters for every profile in config.json (imported lazily: job_crawler reads config at import)."""
    import job_crawler as jc
    out = {}
    for name, prof in jc.RAW_CONFIG.get("profiles", {}).items():
        _, cfg = jc.resolve_profile(jc.RAW_CONFIG, name)
        out[name] = jc.ProfileTitleFilter(cfg)
    return out


def quality_flags(rec):
    flags = []
    raw = rec.get("title", "")
    if ji.repair_mojibake(raw) != raw:
        flags.append("mojibake_repaired_in_display_title")
    cleaned = ji.clean_title(raw)
    if cleaned != ji.normalize_text(raw):
        flags.append("title_noise_or_location_removed_in_display_title")
    if cleaned[:1].islower() and len(cleaned.split()) >= 4:
        flags.append("title_looks_like_description_fragment")
    if ji.is_shared_form_url(rec.get("url", "")):
        flags.append("shared_application_form_url")
    if (rec.get("location") or "").strip().lower() == "israel":
        flags.append("generic_location_israel")
    return flags


def classify_record(rec, filters):
    title, url = rec.get("title", ""), rec.get("url", "")
    provider, jid = ji.extract_ats_job_id(url)
    level, evidence = ji.experience_from_legacy(rec.get("experience", ""), title)
    reasons = []
    nj = ji.classify_non_job(title, url)
    if nj:
        reasons.append(nj)
    if ji.location_is_foreign(title, url):
        reasons.append("non_israel_location")
    display = ji.clean_title(title)
    return {
        "record_version": 2,
        "job_key": ji.job_key(rec.get("company", ""), title, url, rec.get("location", "") + " " + title),
        "ats_provider": provider,
        "ats_job_id": jid,
        "canonical_url": ji.canonicalize_url(url),
        "display_title": display,
        "location_token": "foreign" if "non_israel_location" in reasons else ji.normalize_location(f"{rec.get('location', '')} {title}"),
        "status": "quarantined" if reasons else "active",
        "status_reasons": reasons,
        "duplicate_of": None,
        "experience_level": level,
        "experience_evidence": evidence,
        "profiles": sorted(n for n, f in filters.items() if f.matches(display)),
        "first_seen_at": rec.get("discovered_at"),
        "last_seen_at": rec.get("discovered_at"),
        "quality_flags": quality_flags(rec),
        "source_type": rec.get("source_type") or "legacy_v4",
    }


LEVEL_RANK = {lv: i for i, lv in enumerate(["unspecified", "explicit_junior", "explicit_student", "explicit_0_2",
                                              "stretch_2_plus", "senior"])}


def migrate(records, filters):
    """Pure function: returns (new_records, report). Input records are not mutated."""
    out = []
    for rec in records:
        new = dict(rec)
        # Records already written by the v6 crawler keep their crawler-derived fields; legacy ones get computed fields.
        if rec.get("record_version") == 2 and rec.get("source_type") not in (None, "legacy_v4"):
            out.append(new)
            continue
        new.update(classify_record(rec, filters))
        out.append(new)

    groups = defaultdict(list)
    for i, r in enumerate(out):
        groups[r["job_key"]].append(i)

    dup_examples = []
    for key, idxs in groups.items():
        if len(idxs) < 2:
            continue
        idxs_sorted = sorted(idxs, key=lambda i: (out[i].get("discovered_at") or "", i))
        canon = idxs_sorted[0]
        canon_rec = out[canon]
        canon_hash = ji.legacy_hash(canon_rec["company"], canon_rec["title"], canon_rec["url"])
        seen_times = [out[i].get("discovered_at") for i in idxs if out[i].get("discovered_at")]
        canon_rec["first_seen_at"] = min(seen_times) if seen_times else canon_rec.get("first_seen_at")
        canon_rec["last_seen_at"] = max(seen_times) if seen_times else canon_rec.get("last_seen_at")
        # Most specific experience evidence in the group wins (never silently overwrite with 'unspecified').
        best = max(idxs, key=lambda i: (LEVEL_RANK.get(out[i]["experience_level"], 0), bool(out[i]["experience_evidence"])))
        canon_rec["experience_level"] = out[best]["experience_level"]
        canon_rec["experience_evidence"] = out[best]["experience_evidence"]
        canon_rec["duplicate_count"] = len(idxs) - 1
        for i in idxs_sorted[1:]:
            out[i]["duplicate_of"] = canon_hash
            if out[i]["status"] == "active":
                out[i]["status"] = "duplicate"
                out[i]["status_reasons"] = ["same_job_key_as_earlier_record"]
        dup_examples.append({"job_key": key, "indices": idxs_sorted,
                             "titles": [records[i]["title"] for i in idxs_sorted], "canonical_index": canon})

    shared_form_groups = defaultdict(list)
    for i, r in enumerate(out):
        if ji.is_shared_form_url(r.get("url", "")):
            shared_form_groups[r["canonical_url"]].append(i)

    report = {
        "generated_at": storage.now_iso(),
        "input_records": len(records),
        "output_records": len(out),
        "unique_job_keys": len(groups),
        "status_counts": {s: sum(1 for r in out if r["status"] == s) for s in ("active", "duplicate", "quarantined")},
        "duplicate_groups": dup_examples,
        "shared_form_url_groups_kept_distinct": [{"url": u, "indices": ix, "titles": [records[i]["title"] for i in ix]}
                                                 for u, ix in shared_form_groups.items() if len(ix) > 1],
        "quarantined": [{"index": i, "company": records[i]["company"], "title": records[i]["title"], "url": records[i]["url"],
                         "reasons": out[i]["status_reasons"]} for i, r in enumerate(out) if r["status"] == "quarantined"],
        "experience_level_counts": {lv: sum(1 for r in out if r["experience_level"] == lv) for lv in ji.EXPERIENCE_LEVELS},
        "profile_counts": {},
        "quality_flag_counts": {},
        "legacy_fields_unchanged": all(all(out[i].get(f) == records[i].get(f) for f in LEGACY_FIELDS) for i in range(len(records))),
    }
    for r in out:
        for p in r.get("profiles", []):
            report["profile_counts"][p] = report["profile_counts"].get(p, 0) + 1
        for f in r.get("quality_flags", []):
            report["quality_flag_counts"][f] = report["quality_flag_counts"].get(f, 0) + 1
    return out, report


def _git_head_bytes(path):
    try:
        rel = os.path.relpath(path, BASE_DIR).replace("\\", "/")
        return subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=BASE_DIR, capture_output=True, check=True).stdout
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--file", default=SEEN_FILE)
    a = ap.parse_args(argv)

    records = storage.load_json(a.file, None)
    if not isinstance(records, list):
        raise SystemExit(f"{a.file}: expected a JSON array.")
    filters = _profile_filters()
    new_records, report = migrate(records, filters)

    print(f"input={report['input_records']} output={report['output_records']} unique_keys={report['unique_job_keys']}")
    print("status:", report["status_counts"])
    print("profiles:", report["profile_counts"])
    print("experience:", report["experience_level_counts"])
    print("legacy fields unchanged:", report["legacy_fields_unchanged"])

    if not report["legacy_fields_unchanged"] or report["output_records"] != report["input_records"]:
        raise SystemExit("ABORT: migration would change legacy fields or record count.")
    if a.apply and new_records == records:
        print("already migrated - no changes.")
        return

    os.makedirs(REPORT_DIR, exist_ok=True)
    rpt_path = os.path.join(REPORT_DIR, f"seen_jobs_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}{'' if a.apply else '_dryrun'}.json")
    storage.atomic_write_json(rpt_path, report)
    print("report:", os.path.relpath(rpt_path, BASE_DIR))

    if not a.apply:
        print("dry run only - nothing written to seen_jobs.json. Re-run with --apply.")
        return
    raw = _git_head_bytes(a.file) if os.path.abspath(a.file) == SEEN_FILE else None
    with open(a.file, "rb") as f:
        current = f.read()
    # Archive the exact current bytes; also the git HEAD blob when it differs only by line endings.
    arch, digest = storage.archive_bytes(a.file, "seen_jobs.before_migrate", current)
    if raw and raw != current and json.loads(raw) == json.loads(current):
        storage.archive_bytes(a.file, "seen_jobs.pre_v2.git_head", raw)
    storage.atomic_write_json(a.file, new_records)
    storage.append_audit("seen_jobs_migration_v2", archive=os.path.relpath(arch, BASE_DIR).replace("\\", "/"), archive_sha256=digest,
                         input_records=len(records), output_records=len(new_records), status_counts=report["status_counts"],
                         report=os.path.relpath(rpt_path, BASE_DIR).replace("\\", "/"))
    print(f"applied. archive: {os.path.relpath(arch, BASE_DIR)} (sha256 {digest[:16]}...)")


if __name__ == "__main__":
    sys.exit(main())

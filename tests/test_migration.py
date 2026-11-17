import copy
import json

import migrate_seen_jobs as mig
from conftest import find

LEGACY = mig.LEGACY_FIELDS


def test_all_88_records_kept_and_legacy_fields_untouched(legacy_records, profile_filters):
    before = copy.deepcopy(legacy_records)
    out, report = mig.migrate(legacy_records, profile_filters)
    assert legacy_records == before, "input must not be mutated"
    assert len(out) == 88 == report["output_records"]
    assert report["legacy_fields_unchanged"]
    for old, new in zip(before, out):
        for f in LEGACY:
            assert new[f] == old[f]


def test_migration_is_idempotent(legacy_records, profile_filters):
    once, _ = mig.migrate(legacy_records, profile_filters)
    twice, _ = mig.migrate(once, profile_filters)
    assert json.dumps(once, ensure_ascii=False, sort_keys=True) == json.dumps(twice, ensure_ascii=False, sort_keys=True)


def test_moveo_shared_form_roles_stay_distinct_and_active(legacy_records, profile_filters):
    out, report = mig.migrate(legacy_records, profile_filters)
    idx = find(out, company="Moveo group")
    assert len(idx) == 3
    assert len({out[i]["job_key"] for i in idx}) == 3
    assert all(out[i]["status"] == "active" for i in idx)
    assert report["shared_form_url_groups_kept_distinct"][0]["indices"] == idx


def test_true_duplicates_are_marked_not_deleted(legacy_records, profile_filters):
    out, report = mig.migrate(legacy_records, profile_filters)
    nxt = find(out, url="nextinsurance66/jobs/7805602003")
    assert len(nxt) == 3
    statuses = [out[i]["status"] for i in nxt]
    assert statuses.count("active") == 1 and statuses.count("duplicate") == 2
    canon = out[nxt[0]]
    assert canon["first_seen_at"] == "2026-11-06T23:48:22.974887"
    assert canon["last_seen_at"] == "2026-11-16T14:24:28.217090"
    assert report["status_counts"]["duplicate"] == 8


def test_non_jobs_and_foreign_roles_are_never_active(legacy_records, profile_filters):
    out, _ = mig.migrate(legacy_records, profile_filters)
    for i in find(out, url="ArticlePage.aspx") + find(out, url="hailo.ai/authorization") + find(out, url="bangkok-thailand"):
        assert out[i]["status"] == "quarantined", out[i]["title"]
    travelier = [out[i] for i in find(out, company="Travelier")]
    assert all("non_israel_location" in r["status_reasons"] for r in travelier)


def test_unspecified_experience_is_not_reported_as_0_2(legacy_records, profile_filters):
    out, _ = mig.migrate(legacy_records, profile_filters)
    fe = out[find(out, url="nextinsurance66/jobs/7805602003")[0]]
    assert fe["experience_level"] == "stretch_2_plus"  # "A minimum of 2 years" from a later sighting wins over silence
    wedev = out[find(out, url="wedev/jobs/4871703101")[0]]
    assert wedev["experience_level"] == "explicit_junior"
    assert sum(1 for r in out if r["experience_level"] == "unspecified") > 0


def test_schema_contract_every_record_has_legacy_and_v2_fields(legacy_records, profile_filters):
    out, _ = mig.migrate(legacy_records, profile_filters)
    for r in out:
        for f in LEGACY + mig.ADDED_FIELDS:
            assert f in r, f
        assert r["status"] in ("active", "duplicate", "quarantined")

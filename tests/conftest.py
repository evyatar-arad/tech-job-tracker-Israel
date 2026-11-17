import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def legacy_records():
    with open(os.path.join(FIXTURES, "seen_jobs_legacy_88.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def profile_filters():
    import migrate_seen_jobs
    return migrate_seen_jobs._profile_filters()


def find(records, **kw):
    return [i for i, r in enumerate(records) if all(k in r and v in r[k] for k, v in kw.items())]

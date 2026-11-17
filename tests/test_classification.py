import pytest

import job_identity as ji


@pytest.mark.parametrize("title,url,reason", [
    ("ה-AI לא יחליף אתכם, אבל מי ששולט בו- כן. מדריך הכשרות וכלים ל-2026",
     "https://www.alljobs.co.il/ArticlePage.aspx?LinkWord=free-ai-skills", "article_or_guide"),
    ("Developer Zone", "https://hailo.ai/authorization/?action=logout&redirect_to=%2Fdeveloper-zone%2F&_wpnonce=450debc50f",
     "logout_or_auth_url"),
    ("Entry-Level Roles", "https://careers.linkedin.com/pathways-programs/entry-level", "program_or_portal_page"),
    ("Data Engineer", "https://www.gotfriends.co.il/jobslobby/software/data-engineer/", "category_landing_page"),
    ("Automation Engineer", "https://experis.co.il/search?search=Automation", "search_results_page"),
    ("Students & Internship", "https://careers.towersemi.com/career-opportunities/students-internship/", "program_or_portal_page"),
    ("monday.com Software Development", "https://il.linkedin.com/company/mondaydotcom?trk=similar-pages", "company_profile_page"),
])
def test_non_job_pages_are_detected(title, url, reason):
    assert ji.classify_non_job(title, url) == reason


@pytest.mark.parametrize("title,url", [
    ("Product Manager בחברת סטארט-אפ", "https://www.gotfriends.co.il/jobslobby/projects/product-manager/155204/"),
    ("Frontend Engineer", "https://job-boards.greenhouse.io/nextinsurance66/jobs/7805602003"),
    ("מנהל.ת פרויקטים לארגון מוביל", "https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID=8896603"),
    ("Junior Product Operations – Part-Time Student", "https://jobs.ashbyhq.com/finout/41bf1175-c1b9-4a22-8240-82cebddc59d1/"),
])
def test_real_postings_are_not_flagged(title, url):
    assert ji.classify_non_job(title, url) is None


def test_israeli_company_with_bangkok_role_is_foreign():
    title = "Junior Supply Operation Specialist (Bangkok-based)Bangkok, ThailandSupply Operations"
    url = "https://www.travelier.com/careers/co/bangkok-thailand/41.371/junior-supply-operation-specialist-bangkok-based/all/"
    assert ji.location_is_foreign(title, url)


def test_european_mfd_marker_is_foreign_but_israeli_city_wins():
    assert ji.location_is_foreign("Working Student (m/f/d) – Cloud Development")
    assert not ji.location_is_foreign("Product Analyst", "Tel Aviv-Yafo, Israel")
    assert not ji.location_is_foreign("Operations Analyst - London / Tel Aviv")


@pytest.mark.parametrize("title,text,level", [
    ("Business Analyst", "You will work with stakeholders. Requirements: B.Sc. in Industrial Engineering.", "unspecified"),
    ("Business Analyst", "Requirements: 3+ years of experience as an analyst.", "senior"),
    ("Business Analyst", "At least 3 years of hands-on experience in BI.", "senior"),
    ("Business Analyst", "1-3 years of experience in a similar role.", "explicit_0_2"),
    ("Business Analyst", "2+ years of experience in operations.", "stretch_2_plus"),
    ("Product Operations Student", "Part time, 3 days a week.", "explicit_student"),
    ("Junior Project Coordinator", "", "explicit_junior"),
    ("Senior Product Manager", "0-2 years", "senior"),
    ("אנליסט/ית עסקי/ת", "דרישות: לפחות 3 שנות ניסיון בתפקיד דומה", "senior"),
    ("רכז/ת פרויקטים", "דרישות: תואר בהנדסת תעשייה וניהול", "unspecified"),
])
def test_experience_classification_never_turns_silence_into_0_2(title, text, level):
    assert ji.parse_experience_requirement(title, text)[0] == level


def test_legacy_tracker_labels_are_not_evidence():
    level, ev = ji.experience_from_legacy("0-2 YOE (Deterministic match)", "Frontend Engineer")
    assert level == "unspecified" and ev == ""


def test_match_score_orders_eligible_jobs_above_unspecified_and_senior():
    hi = ji.match_score(1.0, "explicit_junior", "tel aviv", "P1", 0.8, 1.0, None, {"tel aviv"})
    mid = ji.match_score(1.0, "unspecified", "tel aviv", "P1", 0.8, 1.0, None, {"tel aviv"})
    lo = ji.match_score(1.0, "senior", "tel aviv", "P1", 0.8, 1.0, None, {"tel aviv"})
    assert hi > mid > lo
    assert 0 <= lo and hi <= 100

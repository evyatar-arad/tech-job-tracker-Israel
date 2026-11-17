import job_identity as ji


MOVEO_FORM = "https://forms.monday.com/forms/887015da14cd8f19ac195c11bf56607e?r=use1"


def test_shared_application_form_is_not_an_id_and_keeps_titles_distinct():
    assert ji.is_shared_form_url(MOVEO_FORM)
    assert ji.extract_ats_job_id(MOVEO_FORM) == (None, None)
    keys = {ji.job_key("Moveo group", t, MOVEO_FORM, "Israel") for t in (
        "Apply for Junior Full-Stack AI Engineer (opens in a new tab)",
        "Apply for Full-Stack Web Developer  (opens in a new tab)",
        "Apply for Mobile Developer (opens in a new tab)")}
    assert len(keys) == 3


def test_same_greenhouse_posting_with_different_scraped_titles_has_one_key():
    url = "https://job-boards.greenhouse.io/nextinsurance66/jobs/7805602003"
    titles = ["Frontend EngineerKfar Saba, Israel", "Frontend Engineer Kfar Saba, Israel", "Frontend Engineer"]
    assert len({ji.job_key("Next Insurance", t, url, "Israel") for t in titles}) == 1
    assert ji.job_key("Next Insurance", "x", url) == "ats:greenhouse:nextinsurance66/7805602003"


def test_workday_ids_are_scoped_by_tenant_and_comeet_by_company():
    a = ji.extract_ats_job_id("https://paypal.wd1.myworkdayjobs.com/jobs/job/Tel-Aviv/Software-Engineer-Intern_R0138730")
    b = ji.extract_ats_job_id("https://intel.wd1.myworkdayjobs.com/External/job/Israel-Haifa/AI-Product-Analyst_JR0284923")
    assert a == ("workday", "paypal/R0138730")
    assert b == ("workday", "intel/JR0284923")
    k1 = ji.job_key("Earnix", "t", "https://earnix.com/career/79.47a/automation-engineer-student-position/")
    k2 = ji.job_key("Upwind", "t", "https://www.upwind.io/careers/co/tel-aviv/79.47A/x/all")
    assert k1 != k2 and k1.startswith("ats:comeet:earnix:")


def test_linkedin_variants_share_identity():
    a = ji.job_key("Taboola", "Product Analyst", "https://il.linkedin.com/jobs/view/product-analyst-at-taboola-4485937694?position=2&pageNum=0")
    b = ji.job_key("Taboola", "Product Analyst", "https://www.linkedin.com/jobs/view/4485937694")
    assert a == b == "ats:linkedin:4485937694"


def test_canonicalize_url_drops_tracking_but_keeps_meaningful_params():
    u = ji.canonicalize_url("https://WWW.Example.com/jobs/123/?utm_source=x&gh_src=y&id=7#apply")
    assert u == "https://example.com/jobs/123?id=7"
    assert ji.canonicalize_url("https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID=8896603").endswith("JobID=8896603")


def test_clean_title_removes_glued_location_and_cta_noise():
    assert ji.clean_title("Frontend EngineerKfar Saba, Israel") == "Frontend Engineer"
    assert ji.clean_title("Apply for Mobile Developer (opens in a new tab)") == "Mobile Developer"
    assert ji.clean_title("Support Engineer Support Netanya Full-time Apply Now").startswith("Support Engineer")
    assert ji.clean_title("Product Manager") == "Product Manager"
    assert ji.clean_title("דרוש /ה PMO לחברה יצרנית טכנולוגית") == "PMO לחברה יצרנית טכנולוגית"
    assert ji.clean_title("מנהל /ת פרוייקטים") == "מנהל/ת פרוייקטים"
    assert ji.clean_title("Solutions Engineer R&D · Tel Aviv (Hybrid)") == "Solutions Engineer R&D"


def test_mojibake_hebrew_is_repaired_for_display_only():
    broken = "×\x9e×¤×ª×\x97/×ª REACT ×\x9e×ª×\x97×\x99×\x9c/×\x94"
    assert ji.repair_mojibake(broken) == "מפתח/ת REACT מתחיל/ה"
    assert ji.repair_mojibake("Product Manager") == "Product Manager"


def test_legacy_hash_matches_index_html_contract():
    # index.html computes SHA-256 of `${company}::${title}::${url}` (lowercased, trimmed) and keeps the first 16 hex chars.
    assert ji.legacy_hash(" Wix ", "PM", "HTTPS://X") == ji.legacy_hash("wix", "pm", "https://x")
    assert len(ji.legacy_hash("a", "b", "c")) == 16

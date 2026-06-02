from services.triage.sweep import find_gaps


def _issue(num, title, labels, milestone=None, body=""):
    return {"number": num, "title": title, "body": body,
            "labels": [{"name": n} for n in labels], "milestone": milestone}


def test_find_gaps_reports_each_missing_axis():
    issues = [_issue(1, "feat(x): y", labels=["P3-low"])]  # has priority, missing type+area+milestone
    gaps = find_gaps(issues)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["number"] == 1
    assert g["missing_priority"] is False
    assert g["missing_type"] is True
    assert g["missing_area"] is True
    assert g["missing_milestone"] is True
    # the deterministic derive runs here too, so the sweep can apply it
    assert g["derived_type"] == "feature"


def test_find_gaps_skips_fully_triaged_issues():
    issues = [_issue(2, "fix(x): y", labels=["bug", "P2-medium", "backend"],
                     milestone={"title": "Backlog"})]
    assert find_gaps(issues) == []


def test_find_gaps_derived_type_none_when_no_prefix():
    issues = [_issue(3, "No prefix here", labels=["P3-low", "backend"],
                     milestone={"title": "Backlog"})]
    g = find_gaps(issues)[0]
    assert g["missing_type"] is True
    assert g["derived_type"] is None  # cite-or-surface: nothing to auto-apply

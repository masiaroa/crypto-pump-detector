from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_normal_refresh_does_not_block_on_coinalyze_dashboard_refresh():
    refresh = (ROOT / "scripts" / "refresh.sh").read_text(encoding="utf-8")

    assert "coinalyze_refresh.py --force" not in refresh


def test_normal_refresh_delegates_feature_branches_before_scan():
    refresh = (ROOT / "scripts" / "refresh.sh").read_text(encoding="utf-8")

    branch_check = refresh.index('if [ "$BRANCH" != "main" ]')
    delegation = refresh.index("exec ./scripts/branch-refresh.sh")
    scan = refresh.index('echo "[$(date -Iseconds)] === scan ==="')
    assert branch_check < scan
    assert delegation < scan


def test_branch_refresh_does_not_block_on_coinalyze_dashboard_refresh():
    refresh = (ROOT / "scripts" / "branch-refresh.sh").read_text(encoding="utf-8")

    assert "coinalyze_refresh.py --force" not in refresh


def test_branch_refresh_sets_upstream_when_pushing_feature_branch():
    refresh = (ROOT / "scripts" / "branch-refresh.sh").read_text(encoding="utf-8")

    assert 'git push -u origin "$BRANCH"' in refresh


def test_branch_refresh_pushes_even_when_scan_has_no_new_diff():
    refresh = (ROOT / "scripts" / "branch-refresh.sh").read_text(encoding="utf-8")

    no_diff_branch = refresh.index("Sin diffs respecto al repo")
    diff_block_end = refresh.index("\nfi\n\ngit push -u origin \"$BRANCH\"")
    workflow = refresh.index("gh workflow run pages.yml")
    assert no_diff_branch < diff_block_end < workflow

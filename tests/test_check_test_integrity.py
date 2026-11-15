import subprocess

import pytest

from scripts import check_test_integrity as cti
from scripts.check_test_integrity import find_violations, is_approved, parse_diff


def violations(diff_text):
    return find_violations(parse_diff(diff_text))


REMOVED_PY_TEST = """\
diff --git a/tests/test_app.py b/tests/test_app.py
index 1111111..2222222 100644
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -10,3 +9,0 @@ def test_keep():
-def test_health_returns_ok():
-    assert get("/health") == 200
-
"""

ADDED_PY_SKIP = """\
diff --git a/tests/test_app.py b/tests/test_app.py
index 1111111..2222222 100644
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -9,0 +10 @@
+@pytest.mark.skip(reason="flaky")
@@ -20,0 +22,2 @@ def test_other():
+    if True: pytest.skip("later")
+    pytest.xfail("broken")
"""

RENAMED_PY_TEST = """\
diff --git a/tests/test_app.py b/tests/test_app.py
index 1111111..2222222 100644
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -10 +10 @@
-def test_helth():
+def test_health():
"""

RENAMED_TEST_FILE = """\
diff --git a/tests/test_old.py b/tests/test_new.py
similarity index 100%
rename from tests/test_old.py
rename to tests/test_new.py
"""

DELETED_TEST_FILE = """\
diff --git a/tests/test_gone.py b/tests/test_gone.py
deleted file mode 100644
index 1111111..0000000
--- a/tests/test_gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def test_one():
-    assert True
"""

MOVED_TEST_BETWEEN_FILES = """\
diff --git a/tests/test_a.py b/tests/test_a.py
index 1111111..2222222 100644
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,2 +0,0 @@
-def test_moved():
-    assert True
diff --git a/tests/test_b.py b/tests/test_b.py
index 3333333..4444444 100644
--- a/tests/test_b.py
+++ b/tests/test_b.py
@@ -5,0 +6,2 @@
+def test_moved():
+    assert True
"""

JS_CHANGES = """\
diff --git a/web/app.test.ts b/web/app.test.ts
index 1111111..2222222 100644
--- a/web/app.test.ts
+++ b/web/app.test.ts
@@ -3,3 +3,3 @@
-  it('renders the header', () => {
+  it.only('renders the footer', () => {
-  test("submits the form", async () => {
+  xit("other", () => {
+  describe.skip('suite', () => {
"""

STRING_AND_NON_TEST_CHANGES = """\
diff --git a/docs/notes.md b/docs/notes.md
index 1111111..2222222 100644
--- a/docs/notes.md
+++ b/docs/notes.md
@@ -1 +1 @@
-def test_in_markdown():
+@pytest.mark.skip in prose
diff --git a/tests/test_fixtures.py b/tests/test_fixtures.py
index 1111111..2222222 100644
--- a/tests/test_fixtures.py
+++ b/tests/test_fixtures.py
@@ -1,0 +2,3 @@
+FIXTURE = '''
++@pytest.mark.skip
+-def test_removed():
"""


def test_removed_python_test_fails():
    assert violations(REMOVED_PY_TEST) == [
        "removed test: tests/test_app.py::test_health_returns_ok"
    ]


def test_added_python_skip_markers_fail():
    found = violations(ADDED_PY_SKIP)
    assert len(found) == 3
    assert all(v.startswith("added skip marker in tests/test_app.py") for v in found)


def test_renamed_test_function_is_allowed():
    assert violations(RENAMED_PY_TEST) == []


def test_renamed_test_file_is_allowed():
    assert violations(RENAMED_TEST_FILE) == []


def test_deleted_test_file_fails():
    assert violations(DELETED_TEST_FILE) == [
        "deleted test file: tests/test_gone.py",
        "removed test: tests/test_gone.py::test_one",
    ]


def test_test_moved_between_files_is_allowed():
    assert violations(MOVED_TEST_BETWEEN_FILES) == []


def test_js_skip_only_and_removed_test():
    found = violations(JS_CHANGES)
    markers = [v for v in found if v.startswith("added skip marker")]
    removed = [v for v in found if v.startswith("removed test")]
    assert len(markers) == 3
    # Two tests removed and two new names added in the same file
    # ("renders the footer", "other"), so the removals count as renames and
    # only the skip/only markers are reported.
    assert removed == []


def test_js_removed_test_without_replacement_fails():
    diff = """\
diff --git a/src/__tests__/api.js b/src/__tests__/api.js
--- a/src/__tests__/api.js
+++ b/src/__tests__/api.js
@@ -4 +3,0 @@
-test(`returns 404`, () => {
"""
    assert violations(diff) == ["removed test: src/__tests__/api.js::returns 404"]


def test_marker_text_in_strings_and_non_code_files_is_ignored():
    assert violations(STRING_AND_NON_TEST_CHANGES) == []


def _labeled(event, login, name=cti.APPROVAL_LABEL):
    return {"event": event, "actor": {"login": login}, "label": {"name": name}}


def test_approved_label_by_owner_passes():
    timeline = [{"event": "commented"}, _labeled("labeled", "Raj9914")]
    assert is_approved(timeline, "raj9914") is True


@pytest.mark.parametrize(
    "timeline",
    [
        [],
        [_labeled("labeled", "some-collaborator")],
        [_labeled("labeled", "Raj9914"), _labeled("unlabeled", "Raj9914")],
        [_labeled("labeled", "Raj9914"), _labeled("unlabeled", "x"), _labeled("labeled", "x")],
        [_labeled("labeled", "Raj9914", name="other-label")],
    ],
)
def test_approval_rejected_unless_owner_added_label_last(timeline):
    assert is_approved(timeline, "Raj9914") is False


def test_main_uses_approval_only_when_violations(monkeypatch, capsys):
    monkeypatch.setattr(cti, "_git_diff", lambda base, head: REMOVED_PY_TEST)
    monkeypatch.setattr(
        cti, "_fetch_timeline", lambda repo, pr: [_labeled("labeled", "Raj9914")]
    )
    assert cti.main(["base", "head", "--repo", "Raj9914/sandbox", "--pr", "5"]) == 0
    assert "APPROVED" in capsys.readouterr().out

    monkeypatch.setattr(
        cti, "_fetch_timeline", lambda repo, pr: [_labeled("labeled", "intruder")]
    )
    assert cti.main(["base", "head", "--repo", "Raj9914/sandbox", "--pr", "5"]) == 1

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("PR_NUMBER", raising=False)
    assert cti.main(["base", "head"]) == 1


def test_main_fails_closed_when_timeline_unavailable(monkeypatch, capsys):
    def boom(repo, pr):
        raise subprocess.CalledProcessError(1, ["gh"])

    monkeypatch.setattr(cti, "_git_diff", lambda base, head: ADDED_PY_SKIP)
    monkeypatch.setattr(cti, "_fetch_timeline", boom)
    assert cti.main(["b", "h", "--repo", "o/r", "--pr", "1"]) == 1
    assert "could not read PR timeline" in capsys.readouterr().out


def test_main_against_real_git_repo(tmp_path, monkeypatch, capsys):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_x.py"
    test_file.write_text("def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("PR_NUMBER", raising=False)

    assert cti.main(["main", "HEAD"]) == 0
    assert capsys.readouterr().out.startswith("OK")

    git("checkout", "-q", "-b", "feature")
    test_file.write_text("def test_a():\n    assert True\n")
    git("commit", "-q", "-am", "drop test_b")
    assert cti.main(["main", "HEAD"]) == 1
    assert "removed test: tests/test_x.py::test_b" in capsys.readouterr().out

    assert cti.main(["no-such-ref", "HEAD"]) == 2

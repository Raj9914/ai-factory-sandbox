"""Tests for scripts/experiment_metrics.py (run: python -m pytest -q tests/test_experiment_metrics.py)."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error

import pytest

TEMPLATE = Path(__file__).resolve().parents[1]
SCRIPT = TEMPLATE / "scripts" / "experiment_metrics.py"
EVENTS = TEMPLATE / "analytics" / "events.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "experiment_metrics"
EXPERIMENT = "demo-20261111"
SINCE = "2026-11-11T09:00:00Z"
UNTIL = "2026-11-13T09:00:00Z"
FAKE_KEY = "phx_test_not_a_real_key_000000"


def load():
    spec = importlib.util.spec_from_file_location("experiment_metrics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep scripts/__pycache__ out of the template tree
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


em = load()


@pytest.fixture
def experiments(tmp_path):
    root = tmp_path / "experiments"
    shutil.copytree(FIXTURES / EXPERIMENT, root / EXPERIMENT)
    return root


def run_main(experiments_dir, *extra, env=None, urlopen=None):
    argv = ["--experiment", EXPERIMENT, "--since", SINCE, "--until", UNTIL,
            "--experiments-dir", str(experiments_dir), "--events", str(EVENTS), *extra]
    kwargs = {"env": env or {}}
    if urlopen is not None:
        kwargs["urlopen"] = urlopen
    return em.main(argv, **kwargs)


def write_counts(tmp_path, counts):
    path = tmp_path / "counts.json"
    path.write_text(json.dumps({"counts": counts}), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- golden fixture run

def test_fixture_run_matches_golden_files(experiments):
    assert run_main(experiments, "--fixture", str(FIXTURES / "counts.json")) == 0
    folder = experiments / EXPERIMENT
    golden = FIXTURES / "golden"
    assert (folder / "metrics.md").read_text(encoding="utf-8") == \
        (golden / "metrics.md").read_text(encoding="utf-8")
    assert (folder / "metrics.json").read_text(encoding="utf-8") == \
        (golden / "metrics.json").read_text(encoding="utf-8")


def test_cli_fixture_run_from_template_root_matches_golden(tmp_path):
    """Documented usage: run from the product repo root with default paths."""
    root = tmp_path / "product"
    shutil.copytree(FIXTURES / EXPERIMENT, root / "experiments" / EXPERIMENT)
    (root / "analytics").mkdir()
    shutil.copy(EVENTS, root / "analytics" / "events.json")
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--experiment", EXPERIMENT, "--since", SINCE,
         "--until", UNTIL, "--fixture", str(FIXTURES / "counts.json")],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "error_rate: 3/0 -> INSUFFICIENT DATA" in result.stdout
    written = root / "experiments" / EXPERIMENT / "metrics.md"
    assert written.read_text(encoding="utf-8") == \
        (FIXTURES / "golden" / "metrics.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- insufficient data

def test_zero_denominator_is_insufficient_data_never_a_rate(experiments, tmp_path):
    # every denominator event (app_opened, activation, core_action_completed) is 0
    counts = write_counts(tmp_path, {"error_unhandled": 5})
    assert run_main(experiments, "--fixture", str(counts)) == 0
    report = json.loads((experiments / EXPERIMENT / "metrics.json").read_text(encoding="utf-8"))
    for metric in report["metrics"]:
        assert metric["denominator"] == 0
        assert metric["status"] == "INSUFFICIENT DATA"
        assert metric["rate"] is None
        assert metric["threshold_met"] is None
    md = (experiments / EXPERIMENT / "metrics.md").read_text(encoding="utf-8")
    funnel = md.split("## Funnel metrics", 1)[1]
    rows = [line for line in funnel.splitlines() if line.startswith("| ") and "`" in line]
    assert len(rows) == 3
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[3] == "INSUFFICIENT DATA"   # Rate column
        assert cells[6] == "INSUFFICIENT DATA"   # Met? column
    assert "inf" not in md.lower() and "nan" not in md.lower()


def test_compute_zero_denominator_with_zero_numerator():
    experiment = {"metrics": [{"name": "m", "label": "m", "numerator": "a", "denominator": "b",
                               "threshold": 0.0, "direction": "at_least", "min_sample": 1}]}
    [result] = em.compute(experiment, {"a": 0, "b": 0})
    assert result["rate"] is None and result["status"] == "INSUFFICIENT DATA"


def test_below_min_sample_is_insufficient_data(experiments, tmp_path):
    counts = write_counts(tmp_path, {"app_opened": 29, "activation": 29})  # min_sample is 30
    assert run_main(experiments, "--fixture", str(counts)) == 0
    report = json.loads((experiments / EXPERIMENT / "metrics.json").read_text(encoding="utf-8"))
    activation = report["metrics"][0]
    assert activation["name"] == "activation_rate"
    assert activation["rate"] is None and activation["status"] == "INSUFFICIENT DATA"


def test_threshold_directions():
    base = {"name": "m", "label": "m", "numerator": "a", "denominator": "b", "min_sample": 1}
    experiment = {"metrics": [dict(base, threshold=0.5, direction="at_least"),
                              dict(base, threshold=0.5, direction="at_most")]}
    at_least, at_most = em.compute(experiment, {"a": 1, "b": 2})
    assert at_least["status"] == "MET" and at_most["status"] == "MET"   # boundary is inclusive
    at_least, at_most = em.compute(experiment, {"a": 3, "b": 4})
    assert at_least["status"] == "MET" and at_most["status"] == "NOT MET"


# --------------------------------------------------------------------------- experiment.yml

def test_parse_experiment_yaml_subset():
    text = (FIXTURES / EXPERIMENT / "experiment.yml").read_text(encoding="utf-8")
    data = em.parse_experiment_yaml(text)
    assert data["id"] == EXPERIMENT
    assert data["hour_0"] == "2026-11-11T09:00:00Z"
    assert [m["name"] for m in data["metrics"]] == ["activation_rate", "core_action_rate", "error_rate"]
    assert data["metrics"][1]["label"] == "Core action rate"
    assert data["metrics"][0]["threshold"] == 0.25 and data["metrics"][0]["min_sample"] == 30


@pytest.mark.parametrize("text, message", [
    ("id: a\nid: b\n", "duplicate key"),
    ("id: a\n  stray: 1\n", "unexpected indentation"),
    ("metrics:\n  name: x\n", "expected a list item"),
    ("just text\n", "expected 'key: value'"),
])
def test_parse_experiment_yaml_rejects_unsupported_syntax(text, message):
    with pytest.raises(em.MetricsError, match=message):
        em.parse_experiment_yaml(text)


@pytest.mark.parametrize("old, new, message", [
    ("denominator: app_opened", "denominator: signup", "'signup' is not an event"),
    ("threshold: 0.25", "threshold: 25", "threshold must be a rate between 0 and 1"),
    ("direction: at_most", "direction: below", "direction must be one of"),
    ("id: demo-20261111", "id: other-20261111", "id must equal the experiment folder"),
    ("hour_0: 2026-11-11T09:00:00Z", "hour_0: 2026-11-11T09:00:00", "must include a timezone"),
    ("min_sample: 30", "min_sample: 0", "min_sample must be a positive integer"),
])
def test_invalid_experiment_fails_without_writing(experiments, capsys, old, new, message):
    path = experiments / EXPERIMENT / "experiment.yml"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    assert run_main(experiments, "--fixture", str(FIXTURES / "counts.json")) == 1
    assert message in capsys.readouterr().err
    assert not (experiments / EXPERIMENT / "metrics.md").exists()
    assert not (experiments / EXPERIMENT / "metrics.json").exists()


def test_fixture_with_unknown_event_fails(experiments, tmp_path, capsys):
    counts = write_counts(tmp_path, {"signup": 3})
    assert run_main(experiments, "--fixture", str(counts)) == 1
    assert "'signup' is not an event" in capsys.readouterr().err


def test_until_before_since_fails(experiments, capsys):
    argv = ["--experiment", EXPERIMENT, "--since", UNTIL, "--until", SINCE,
            "--experiments-dir", str(experiments), "--events", str(EVENTS),
            "--fixture", str(FIXTURES / "counts.json")]
    assert em.main(argv, env={}) == 1
    assert "--until must be after --since" in capsys.readouterr().err


def test_experiment_id_cannot_escape_experiments_dir(experiments, capsys):
    argv = ["--experiment", "../evil", "--since", SINCE, "--experiments-dir", str(experiments),
            "--events", str(EVENTS), "--fixture", str(FIXTURES / "counts.json")]
    assert em.main(argv, env={}) == 1
    assert "invalid experiment ID" in capsys.readouterr().err


# --------------------------------------------------------------------------- PostHog query

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def posthog_env(**overrides):
    env = {"POSTHOG_PERSONAL_API_KEY": FAKE_KEY, "POSTHOG_PROJECT_ID": "12345"}
    env.update(overrides)
    return env


def test_posthog_query_request_and_report(experiments, capsys):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        rows = [["app_opened", 40], ["activation", 12], ["error_unhandled", 3]]
        return FakeResponse(json.dumps({"results": rows}).encode())

    assert run_main(experiments, env=posthog_env(), urlopen=fake_urlopen) == 0
    [(request, timeout)] = calls
    assert timeout == em.QUERY_TIMEOUT_S
    assert request.full_url == "https://us.posthog.com/api/projects/12345/query/"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    query = json.loads(request.data)["query"]
    assert query["kind"] == "HogQLQuery"
    hogql = query["query"]
    assert "count(DISTINCT person_id)" in hogql
    assert "event IN ('activation', 'app_opened', 'core_action_completed', 'error_unhandled')" in hogql
    assert "timestamp >= toDateTime('2026-11-11 09:00:00', 'UTC')" in hogql
    assert "timestamp < toDateTime('2026-11-13 09:00:00', 'UTC')" in hogql
    assert "properties.internal" in hogql  # internal traffic excluded

    folder = experiments / EXPERIMENT
    report = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    assert report["source"] == "PostHog query API (HogQL, unique users)"
    assert report["counts"]["core_action_completed"] == 0  # missing row counts as zero
    golden = json.loads((FIXTURES / "golden" / "metrics.json").read_text(encoding="utf-8"))
    assert report["metrics"] == golden["metrics"]
    outputs = (folder / "metrics.md").read_text(encoding="utf-8") + \
        (folder / "metrics.json").read_text(encoding="utf-8") + "".join(capsys.readouterr())
    assert FAKE_KEY not in outputs and "12345" not in outputs


def test_posthog_host_override_and_https_required(experiments, capsys):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return FakeResponse(b'{"results": []}')

    env = posthog_env(POSTHOG_HOST="https://eu.posthog.com/")
    assert run_main(experiments, env=env, urlopen=fake_urlopen) == 0
    assert seen == ["https://eu.posthog.com/api/projects/12345/query/"]
    env = posthog_env(POSTHOG_HOST="http://posthog.example.com")
    assert run_main(experiments, env=env, urlopen=fake_urlopen) == 1
    assert "POSTHOG_HOST must use https://" in capsys.readouterr().err


def test_posthog_requires_env(experiments, capsys):
    assert run_main(experiments, env={}) == 1
    assert "POSTHOG_PERSONAL_API_KEY" in capsys.readouterr().err
    assert not (experiments / EXPERIMENT / "metrics.md").exists()


def test_posthog_http_error_does_not_leak_key(experiments, capsys):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    assert run_main(experiments, env=posthog_env(), urlopen=fake_urlopen) == 1
    err = capsys.readouterr().err
    assert "HTTP 401" in err and FAKE_KEY not in err
    assert not (experiments / EXPERIMENT / "metrics.json").exists()


@pytest.mark.parametrize("payload", [
    {"error": "nope"},
    {"results": [["app_opened", -1]]},
    {"results": [["signup", 4]]},
    {"results": [["app_opened"]]},
])
def test_posthog_unexpected_payload_fails(experiments, capsys, payload):
    def fake_urlopen(request, timeout):
        return FakeResponse(json.dumps(payload).encode())

    assert run_main(experiments, env=posthog_env(), urlopen=fake_urlopen) == 1
    assert "PostHog query returned" in capsys.readouterr().err


def test_build_hogql_rejects_non_snake_case_event():
    with pytest.raises(em.MetricsError):
        em.build_hogql(["x'); DROP"], em.parse_iso(SINCE, "since"), None)

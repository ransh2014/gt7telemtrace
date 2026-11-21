"""Guards for the Kotlin ports of the 1.2.0 tools (coach, setup advisor, race engineer, Driver DNA, planner, lap
validation, groups, dyno compare, 3D replay). The Kotlin itself is compiled and unit-tested by CI against
android/telemetry/src/test/resources/golden4.json.gz; these checks run everywhere and catch the cheap drift:

* every threshold constant in a Python module is the same number in its Kotlin port,
* every port has a file and a golden test that mentions it,
* golden4.json.gz exists, parses and covers every module (so a stale or half-written file fails here, not in CI).
"""
import gzip
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "src" / "gt7telem"
KT = ROOT / "android" / "telemetry" / "src" / "main" / "kotlin" / "app" / "trace" / "telemetry"
TEST = ROOT / "android" / "telemetry" / "src" / "test"

PORTS = [
    ("coach", "Coach.kt"), ("setup_advisor", "SetupAdvisor.kt"), ("engineer", "Engineer.kt"), ("driver_dna", "DriverDna.kt"),
    ("planner", "Planner.kt"), ("validate", "Validate.kt"), ("groups", "Groups.kt"), ("dyno_compare", "DynoCompare.kt"),
    ("replay3d", "ReplayHtml.kt"), ("corners", "Corners.kt"), ("analysis_tools", "AnalysisTools.kt"), ("llm", "LlmGuard.kt"),
    ("sharelink", "ShareLap.kt"),
]


# llm.py also pins download sizes and the like that only the desktop needs; these are the ones the phone shares
ONLY = {"llm": {"MAX_CHARS", "TEMPERATURE", "MAX_TOKENS", "TIMEOUT_S", "CONTEXT", "MAX_THREADS"}}


def _consts_py(name):
    out = {}
    for m in re.finditer(r"^([A-Z][A-Z0-9_]+)\s*=\s*(-?[0-9][0-9_]*\.?[0-9]*(?:e-?\d+)?)\s*(?:#.*)?$",
                         (PY / f"{name}.py").read_text(encoding="utf-8"), re.M):
        if name not in ONLY or m.group(1) in ONLY[name]:
            out[m.group(1)] = float(m.group(2).replace("_", ""))
    return out


def _consts_kt(file):
    out = {}
    for m in re.finditer(r"const val ([A-Z][A-Z0-9_]+)\s*=\s*(-?[0-9][0-9_]*\.?[0-9]*(?:e-?\d+)?)",
                         (KT / file).read_text(encoding="utf-8")):
        out[m.group(1)] = float(m.group(2).replace("_", ""))
    return out


@pytest.mark.parametrize("module,file", PORTS)
def test_threshold_constants_match(module, file):
    py, kt = _consts_py(module), _consts_kt(file)
    missing = [k for k in py if k not in kt]
    wrong = {k: (py[k], kt[k]) for k in py if k in kt and abs(py[k] - kt[k]) > 1e-12}
    assert not missing, f"{file} lacks constants from {module}.py: {missing}"
    assert not wrong, f"{file} differs from {module}.py: {wrong}"


def test_every_port_exists_and_is_covered_by_the_golden_test():
    test_src = (TEST / "kotlin" / "app" / "trace" / "telemetry" / "Golden4Test.kt").read_text(encoding="utf-8")
    for obj in ("Coach", "LiveCoach", "SetupAdvisor", "Engineer", "DriverDna", "Planner", "Validate", "Groups", "DynoCompare",
                "ReplayHtml", "AnalysisTools", "LlmGuard", "ShareLap"):
        assert f"{obj}." in test_src or f"{obj}(" in test_src, f"Golden4Test.kt does not exercise {obj}"
    for _mod, file in PORTS:
        assert (KT / file).exists(), file


def test_golden4_is_present_and_complete():
    g = json.loads(gzip.open(TEST / "resources" / "golden4.json.gz").read())
    for key in ("coach", "live_coach", "validate", "validate_cases", "setup", "setup_variants", "setup_ab", "engineer_window",
                "engineer_under", "engineer_over", "engineer_sim", "dna", "planner", "dyno_compare", "replay", "groups", "reports",
                "synth", "share"):
        assert key in g, key
    assert g["share"]["big"]["n"] == 3000 and g["share"]["real"]["json"].startswith('{"v":1,')
    assert any(v["tips"] for v in g["coach"].values()), "the coach golden must contain real tips"
    assert [c for c in g["live_coach"]["cues"] if c], "the live-coach golden must contain spoken cues"
    assert g["planner"]["drills"], "the planner golden must contain drills"
    assert g["groups"]["codes_to_decode"]["challenge"].startswith("TRC1-")
    kinds = {c["verdict"] for c in g["validate_cases"]}
    assert {"clean", "suspect", "invalid"} <= kinds


def test_golden4_is_up_to_date_with_the_python_modules(tmp_path):
    """Regenerating the golden file must reproduce it exactly: if a Python module changed, regenerate it
    (python android/tools/make_fixtures4.py) and the Kotlin tests will then check the port against the new numbers."""
    import os
    import subprocess
    import sys
    before = (TEST / "resources" / "golden4.json.gz").read_bytes()
    out = subprocess.run([sys.executable, str(ROOT / "android" / "tools" / "make_fixtures4.py")], cwd=ROOT,
                         capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stderr[-2000:]
    after = (TEST / "resources" / "golden4.json.gz").read_bytes()
    # Strings, ints, keys and structure must match exactly. Floats are compared to 1e-6 (relative), like the Kotlin tests do:
    # numpy/pandas on another Python version or platform can differ in the last digits of a long float, which is not "stale".
    problems = _json_diff(json.loads(gzip.decompress(before)), json.loads(gzip.decompress(after)), "$")
    assert not problems, "golden4.json.gz is stale: run  python android/tools/make_fixtures4.py  (" + "; ".join(problems[:5]) + ")"


def _json_diff(a, b, path):
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return [] if abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b)) else [f"{path}: {a} != {b}"]
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [f"{path}: keys differ {sorted(set(a) ^ set(b))[:5]}"]
        return [p for k in a for p in _json_diff(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += _json_diff(x, y, f"{path}[{i}]")
            if len(out) > 5:
                break
        return out
    return [] if a == b else [f"{path}: {str(a)[:60]!r} != {str(b)[:60]!r}"]

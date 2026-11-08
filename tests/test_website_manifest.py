"""The website's totalfiles.html lists every file in the Python Source download and states how many there are.
That count drifted before (the page said 34 while the zip had 37), so check it against the real zip, and check
that every package file is listed.

The website is a separate repository, normally checked out next to this one (../gt7telemwebsite). When it is not
there (CI for this repository) the test is skipped; the website's own deploy workflow runs the same check."""
import importlib.util
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT.parent / "gt7telemwebsite" / "totalfiles.html"

pytestmark = pytest.mark.skipif(not PAGE.is_file(), reason="website repo not checked out next to this one")


def _zip_names(tmp_path):
    spec = importlib.util.spec_from_file_location("build_source_zip", ROOT / "tools" / "build_source_zip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.build(tmp_path / "gt7telem-source.zip")
    with zipfile.ZipFile(out) as z:
        return z.namelist()


def _source_section():
    html = PAGE.read_text(encoding="utf-8")
    start = html.index("gt7telem-source.zip</h2>")
    end = html.index("</table>", start)
    return html[start:end], html[end:end + 800]


def test_source_file_count_matches_the_zip(tmp_path):
    names = _zip_names(tmp_path)
    _, tail = _source_section()
    shown = int(re.search(r"(\d+) files", tail).group(1))
    assert shown == len(names), f"totalfiles.html says {shown} files, the source zip has {len(names)}"


def test_every_zip_file_is_listed(tmp_path):
    section, _ = _source_section()
    listed = set(re.findall(r"<tr><td>([^<]+)</td>", section))
    missing = []
    for name in _zip_names(tmp_path):
        # the page lists package modules by bare name (dashboard.py) and a few with their folder (gt7telem/fonts/...)
        bare = name.removeprefix("gt7telem/")
        if name not in listed and bare not in listed and Path(name).name not in listed:
            missing.append(name)
    assert not missing, f"not listed on totalfiles.html: {missing}"

"""Lea's pages are found wherever she puts them, and the checker catches switching problems."""
import os

from lookat.check_frontend import check
from lookat.server import FALLBACK_DIR, find_page

GOOD = "<html><script>fetch('/api/screen')</script></html>"


def write(path, text=GOOD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_fallback_until_lea_has_a_page(tmp_path):
    path, lea = find_page("patient", str(tmp_path))
    assert not lea and path == os.path.join(FALLBACK_DIR, "patient.html") and os.path.isfile(path)


def test_lea_plain_file_wins(tmp_path):
    write(tmp_path / "patient.html")
    assert find_page("patient", str(tmp_path)) == (str(tmp_path / "patient.html"), True)


def test_folder_index_and_build_output(tmp_path):
    write(tmp_path / "nurse" / "index.html")
    assert find_page("nurse", str(tmp_path))[0] == str(tmp_path / "nurse" / "index.html")
    write(tmp_path / "dist" / "nurse.html")
    assert find_page("nurse", str(tmp_path))[0] == str(tmp_path / "dist" / "nurse.html")


def test_missing_page_without_fallback_is_none(tmp_path):
    assert find_page("panel", str(tmp_path), fallback_dir=str(tmp_path / "none")) == (None, False)


def levels(results, level):
    return [m for lv, m in results if lv == level]


def test_check_passes_good_pages(tmp_path):
    write(tmp_path / "patient.html")
    write(tmp_path / "nurse.html", "<script>fetch('/api/nurse')</script>")
    write(tmp_path / "panel.html", "<script src='/web/panel.js'></script>")
    write(tmp_path / "panel.js", "fetch('/api/panel')")
    assert levels(check(str(tmp_path)), "error") == []


def test_check_catches_hardcoded_address_and_missing_api(tmp_path):
    write(tmp_path / "patient.html", "<script>fetch('http://localhost:8000/api/screen')</script>")
    write(tmp_path / "nurse.html", "<p>static mockup</p>")
    errors = levels(check(str(tmp_path)), "error")
    assert any("hard-coded" in e for e in errors)
    assert any("/api/nurse" in e for e in errors)


def test_check_warns_about_internet_files(tmp_path):
    write(tmp_path / "patient.html", GOOD + "<link href='https://fonts.googleapis.com/css2?family=Inter'>")
    assert any("internet" in w for w in levels(check(str(tmp_path)), "warn"))


def test_check_build_project_needs_dist(tmp_path):
    write(tmp_path / "package.json", "{}")
    assert any("dist" in e for e in levels(check(str(tmp_path)), "error"))


def test_check_ignores_node_modules(tmp_path):
    write(tmp_path / "node_modules" / "x.js", "fetch('http://127.0.0.1:9')")
    assert levels(check(str(tmp_path)), "error") == []

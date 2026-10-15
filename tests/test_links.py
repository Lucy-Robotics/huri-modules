"""The links of this repository's pages (scripts/check_links.py)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_links", ROOT / "scripts" / "check_links.py"
)
assert spec and spec.loader
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


def test_the_pages_have_no_broken_links(capsys):
    assert links.main([]) == 0, capsys.readouterr().out


def test_a_broken_link_or_anchor_is_reported(tmp_path):
    (tmp_path / "other.md").write_text("# Some Heading\n")
    page = tmp_path / "page.md"
    page.write_text(
        "[ok](other.md#some-heading) [gone](nope.md) [bad](other.md#nope)\n"
        "[web](https://example.org/x) `[code](ignored.md)`\n"
    )
    problems = links.check_file(page)
    assert len(problems) == 2, problems
    assert "nope.md" in problems[0] and "no heading #nope" in problems[1]


def test_the_old_repository_name_is_reported(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "[front](https://github.com/Lucy-Robotics/huri-core#huri-ecosystem) "
        "[robot](https://github.com/Lucy-Robotics/HuRI-inmoov) "
        "[site](https://github.com/Lucy-Robotics/HuRI_ATP_testsite)\n"
    )
    assert links.check_file(page) == []
    for old in ("HuRI)", "HuRI.git", "huri/tree/dev", "HuRI#readme"):
        page.write_text(f"[old](https://github.com/Lucy-Robotics/{old}\n")
        problems = links.check_file(page)
        assert len(problems) == 1 and "old name of huri-core" in problems[0], old


def test_the_archived_landing_repository_is_reported(tmp_path):
    page = tmp_path / "page.md"
    for old in ("HuRI-landing)", "HuRI-landing/blob/main/ARCHITECTURE.md)"):
        page.write_text(f"[front](https://github.com/Lucy-Robotics/{old}\n")
        problems = links.check_file(page)
        assert len(problems) == 1 and "archived" in problems[0], old
        assert "huri-core#huri-ecosystem" in problems[0]

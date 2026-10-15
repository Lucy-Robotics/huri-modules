#!/usr/bin/env python3
"""Check the links of this repository's Markdown pages (run by CI).

    python scripts/check_links.py [FILE_OR_DIR ...]

A relative link must point to an existing file or directory, and a `#anchor`
into a Markdown file to one of its headings (GitHub's slugs). A URL of
`github.com/Lucy-Robotics/HuRI` is an error: that is the old name of huri-core
(GitHub redirects it, but a page must not rely on that); HuRI's front page is
Lucy-Robotics/HuRI-landing. Other URLs are not fetched: CI does not depend on
the network.

Without arguments it checks the default pages below. A directory is searched
for *.md files, skipping hidden directories and the third-party submodules
under modules/ (their pages are their authors').
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

REPO = Path(__file__).resolve().parents[1]
DEFAULT = [
    "README.md",
    "RELEASING.md",
    "ACCEPTANCE.md",
    "modules/README.md",
    "packages",
    "examples",
]

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# The old name, not HuRI-landing (or any other repository starting with HuRI).
_OLD_NAME = re.compile(r"github\.com/Lucy-Robotics/HuRI(?![\w-])", re.I)


def slug(heading: str) -> str:
    # GitHub: lowercase, drop punctuation but keep "-" and "_", spaces -> "-".
    text = re.sub(r"[`*]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> Set[str]:
    """GitHub's heading anchors; a repeated heading gets `-1`, `-2`, ..."""
    text = _FENCE.sub("", path.read_text())
    seen: Dict[str, int] = {}
    result = set()
    for m in re.finditer(r"(?m)^#+\s+(.*)$", text):
        base = slug(m.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def links(path: Path) -> Iterable[str]:
    return _LINK.findall(_INLINE_CODE.sub("", _FENCE.sub("", path.read_text())))


def check_file(path: Path) -> List[str]:
    problems = [
        f"{path}: {m.group(0)} -> the old name of huri-core; link "
        "Lucy-Robotics/huri-core, or Lucy-Robotics/HuRI-landing for the front page"
        for m in _OLD_NAME.finditer(path.read_text())
    ]
    for target in links(path):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # https:, mailto:, ...
            continue
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / file_part).resolve() if file_part else path
        if not resolved.exists():
            problems.append(f"{path}: {target} -> {resolved} does not exist")
        elif anchor and resolved.suffix == ".md" and anchor not in anchors(resolved):
            problems.append(f"{path}: {target} -> no heading #{anchor}")
    return problems


def markdown_files(path: Path) -> List[Path]:
    if not path.is_dir():
        return [path]
    found = []
    for p in sorted(path.rglob("*.md")):
        parts = p.resolve().relative_to(path.resolve()).parts
        if any(part.startswith(".") for part in parts):
            continue  # .git, .venv, .pytest_cache
        try:
            in_repo = p.resolve().relative_to(REPO).parts
        except ValueError:
            in_repo = ()
        if len(in_repo) > 2 and in_repo[0] == "modules":
            continue  # a third-party submodule's own pages
        found.append(p)
    return found


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "paths", nargs="*", type=Path, default=[REPO / page for page in DEFAULT]
    )
    args = parser.parse_args(argv)

    files = [f for path in args.paths for f in markdown_files(path)]
    problems = [p for f in files for p in check_file(f)]
    for problem in problems:
        print(f"- {problem}")
    print(
        f"{len(files)} file(s), "
        + ("links OK" if not problems else f"{len(problems)} broken")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

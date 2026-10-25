"""The skill read-a-building-model: its rules in SKILL.md, its recipes in the pages beside it (since 0.7.0).

flo2 serves this folder unchanged (flo2's src/helper-skills/read-a-building-model/,
compared byte for byte with the commit it pins), each file in one reply of at
most 30,000 characters of JSON. Until 0.7.0 SKILL.md was flo2's, one page that
every feature added a section to: 4,205 bytes on 2026-10-03, 26,766 on
2026-10-24, its reply 67 characters under that limit. Anthony's ruling of
2026-10-25 (flo2's design 1a97fc1f1de4d5d2) split it: the rules stay in SKILL.md,
the recipes move into reference pages it names, and each file is held to a size
here, so a new feature adds a page or grows one page, never the whole skill.

What must hold:
- each file is at most its budget (SKILL.md and every reference: MAX_BYTES);
- SKILL.md names every reference page, and every page it names exists;
- every ifcmcp or flo2-ifc tool a page names is one the server serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_smoke import EXPECTED_TOOLS

FOLDER = Path(__file__).resolve().parent.parent / "skills" / "read-a-building-model"
#: The most one file of the skill may be. flo2 serves each file as JSON text in
#: one reply of at most 30,000 characters, with its own envelope (about 3,200
#: characters for SKILL.md, measured on 2026-10-25) and escaping (a newline or a
#: quote costs two). 16,000 bytes leaves room for both, and a page that outgrows
#: it is split, not trimmed.
MAX_BYTES = 16_000
#: Words that look like tool names and are arguments: an entity's class, an IDS's IFC version.
ARGUMENTS = {"ifc_class", "ifc_version"}
PAGES = sorted(p.relative_to(FOLDER).as_posix() for p in FOLDER.rglob("*") if p.is_file())


def test_the_skill_is_its_rules_and_the_pages_beside_them():
    assert "SKILL.md" in PAGES
    assert any(p.startswith("references/") for p in PAGES), PAGES


@pytest.mark.parametrize("page", PAGES)
def test_each_file_is_within_its_budget(page):
    size = (FOLDER / page).stat().st_size
    assert size <= MAX_BYTES, f"{page} is {size} bytes, over {MAX_BYTES}: split it into another page SKILL.md names"


def test_skill_md_names_every_page_and_every_page_it_names_exists():
    text = (FOLDER / "SKILL.md").read_text()
    named = set(re.findall(r"`(references/[a-z0-9-]+\.md)`", text))
    pages = {p for p in PAGES if p != "SKILL.md"}
    assert named == pages, f"named but missing: {sorted(named - pages)}; present but not named: {sorted(pages - named)}"


def test_skill_md_has_its_frontmatter():
    text = (FOLDER / "SKILL.md").read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md starts with its frontmatter"
    fields = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    assert fields["name"] == "read-a-building-model"
    assert fields["description"] and fields["compatibility"]


@pytest.mark.parametrize("page", PAGES)
def test_every_tool_a_page_names_is_served(page):
    named = set(re.findall(r"\bifc_[a-z_]+\b", (FOLDER / page).read_text()))
    unknown = sorted(named - EXPECTED_TOOLS - ARGUMENTS)
    assert not unknown, f"{page} names tools the server does not serve: {unknown}"

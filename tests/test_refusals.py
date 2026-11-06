"""A refusal says what was wrong and gives the form to copy (since 0.13.0).

A chat on flo2.io guessed the syntax of ifc_select's selector and of tool arguments, and was refused again and again
over 2026-11-02 to 11-05 (flo2-ifc's design 1510fc3728665dbf,
fact:a-chat-guessed-ifc-select-and-tool-argument-syntax-and-was-refused-repeatedly-2026-11-05, and its root cause,
fact:root-cause-a-refusal-passes-on-the-library-text-and-never-names-the-form-to-copy-2026-11-06). Every refusal
flo2-ifc gave for a malformed call was the text of the library underneath, which the one catch (server._reason_kept)
passes on as the reason: lark's parser for a selector (``No terminal matches '~' ... Expected one of: * DOT``, where
DOT is there because a lowercase ``name`` reads as a property set's name), pydantic's validator for a missing or
mistyped argument, Python's importer for an editing module that does not exist, ifcedit's ``Unknown parameter
'product' for spatial.assign_container`` for a parameter misnamed; and an argument the tool does not take was not
refused at all: it was dropped, so ifc_edit with the function's own arguments beside ``params`` made an
IfcBuildingElementProxy nobody asked for. The same ``~`` was guessed again an hour after its refusal.

These tests hold the class, not today's strings: for every guess of the kinds the field made, through every tool that
takes the thing guessed,

- the call is refused, and nothing in the model changes;
- the refusal carries none of the library's own words (``INTERNALS``);
- within its first 600 characters (what flo2's door passes on: model-door.ts, modelErrorText) it says what was wrong
  and gives the corrected form, built from the chat's own input, in backticks;
- the corrected form, called as given, answers.

And the forms a selector takes are written where a chat reads before its first call: ifc_select's description and
the skill's SKILL.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anyio
import pytest

from flo2_ifc.server import build_server
from tests import models

#: Words of the libraries underneath that a refusal must never pass on: lark's (the selector's parser), pydantic's (the
#: MCP SDK's argument check), Python's importer and unpacking, an attribute lookup on the wrong type.
INTERNALS = (
    "No terminal matches",
    "Expected one of",
    "__ANON",
    "validation error for",
    "errors.pydantic.dev",
    "[type=",
    "Input should be",
    "No module named",
    "not enough values to unpack",
    "object has no attribute",
)
#: How much of a refusal flo2's door passes on to a chat (flo2's services/edge-gateway/src/model-door.ts,
#: modelErrorText, and the edit refusal beside it: `.slice(0, 600)`).
DOOR_KEEPS = 600
SKILL = Path(__file__).resolve().parent.parent / "skills" / "read-a-building-model" / "SKILL.md"


def _open(tmp_path) -> object:
    path = tmp_path / "site.ifc"
    models.measured_site().write(str(path))
    server = build_server()
    anyio.run(lambda: server.call_tool("ifc_load", {"path": str(path)}))
    return server


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A server with the synthetic site open, for calls that change nothing."""
    return _open(tmp_path_factory.mktemp("refusals"))


@pytest.fixture
def fresh(tmp_path):
    """A server of its own, for a test whose calls might change the model."""
    return _open(tmp_path)


def call(server, tool: str, arguments: dict) -> tuple[bool, object]:
    """(refused, what came back): a refusal's text, or the answer as data."""
    from mcp.server.mcpserver.exceptions import ToolError

    async def run():
        try:
            result = await server.call_tool(tool, arguments)
        except ToolError as e:  # what a client receives as an is_error result
            return True, str(e)
        text = "".join(getattr(b, "text", "") for b in result.content)
        try:
            return False, json.loads(text)
        except json.JSONDecodeError:
            return False, text

    return anyio.run(run)


def said(text: str) -> str:
    """A refusal as the door passes it on: without the SDK's preamble, its first 600 characters."""
    return re.sub(r"^Error executing tool \w+: ", "", text)[:DOOR_KEEPS]


def assert_teaches(text: object, *forms: str) -> None:
    assert isinstance(text, str), text
    for word in INTERNALS:
        assert word not in text, f"the refusal passes on the library's own words ({word!r}): {text}"
    for form in forms:
        assert f"`{form}`" in said(text), f"the refusal does not give `{form}` in its first {DOOR_KEEPS} characters: {said(text)}"


def edits_so_far(server) -> int:
    refused, answer = call(server, "ifc_edited", {})
    assert not refused, answer
    return sum(answer["counts"].values())


# ------------------------------------------------------------------ selectors

#: (what a chat sent, the selector it meant). The first three are the field's own (flo2.io's call log, 2026-11-04 and
#: 11-05), with the synthetic site's names for the field's ("102", "footing"); the rest are the same guesses in their
#: other spellings, and two that IfcOpenShell reads and answers with nothing: a value in single quotes, and a class the
#: schema does not have.
GUESSED_SELECTORS = [
    ('IfcSpace, name ~ "Room"', 'IfcSpace, Name *= "Room"'),
    ('IfcFooting, name contains "Foot"', 'IfcFooting, Name *= "Foot"'),
    ("IfcFooting OR IfcPile OR IfcSlab", "IfcFooting + IfcPile + IfcSlab"),
    ("IfcWall || IfcSlab", "IfcWall + IfcSlab"),
    ('IfcWall and Name = "Wall A"', 'IfcWall, Name = "Wall A"'),
    ("IfcElement, NOT IfcWall", "IfcElement, ! IfcWall"),
    ('IfcWall, Name == "Wall A"', 'IfcWall, Name = "Wall A"'),
    ('IfcWall, Name like "Wall"', 'IfcWall, Name *= "Wall"'),
    ('IfcSpace, storey = "GROUND"', 'IfcSpace, location = "GROUND"'),
    ("ifcwall", "IfcWall"),
    ("IfcWall, Name = 'Wall A'", 'IfcWall, Name = "Wall A"'),
    ("IfcRoom", "IfcSpace"),
]


@pytest.mark.parametrize("guess, meant", GUESSED_SELECTORS)
def test_a_guessed_selector_is_refused_with_the_selector_it_meant(site, guess, meant):
    refused, text = call(site, "ifc_select", {"selector": guess})
    assert refused, f"{guess!r} was answered, not refused: {text}"
    assert_teaches(text, meant)
    refused, answer = call(site, "ifc_select", {"selector": meant})
    assert not refused, answer
    assert answer["page"]["total"] > 0, (meant, answer)


#: Every tool that takes a selector, with what else it needs to get as far as reading it. ifc_draw takes one in
#: `highlight` (and `around`, `through`), beside ids and GlobalIds.
SELECTOR_TOOLS = [
    ("ifc_select", "selector", {}),
    ("ifc_extent", "selector", {}),
    ("ifc_footprint", "selector", {}),
    ("ifc_volume", "selector", {}),
    ("ifc_quantify", "selector", {"rule": "IFC4QtoBaseQuantities"}),
    ("ifc_render", "selector", {"output_path": "/nonexistent/render.png"}),
    ("ifc_plot", "selector", {"output_path": "/nonexistent/plot.png"}),
    ("ifc_diff", "selector", {"other_path": "/nonexistent/other.ifc", "output_path": "/nonexistent/diff.json"}),
    ("ifc_draw", "highlight", {"view": "plan", "output_path": "/nonexistent/plan"}),
]


@pytest.mark.parametrize("tool, argument, others", SELECTOR_TOOLS, ids=[t for t, _, _ in SELECTOR_TOOLS])
def test_every_tool_that_takes_a_selector_refuses_a_guessed_one_the_same_way(fresh, tool, argument, others):
    before = edits_so_far(fresh)
    refused, text = call(fresh, tool, {**others, argument: "IfcFooting OR IfcSlab"})
    assert refused, f"{tool} answered: {text}"
    assert_teaches(text, "IfcFooting + IfcSlab")
    assert edits_so_far(fresh) == before, f"{tool} changed the model on a refused selector"


def test_every_form_a_selector_refusal_offers_works(site):
    refused, text = call(site, "ifc_select", {"selector": "IfcWall,"})
    assert refused, text
    assert_teaches(text)
    offered = [s for s in re.findall(r"`([^`]+)`", text) if s.startswith("Ifc") and s != "IfcWall,"]
    assert len(offered) >= 5, f"the refusal offers {len(offered)} forms to copy: {text}"
    for selector in offered:
        refused, answer = call(site, "ifc_select", {"selector": selector})
        assert not refused, (selector, answer)


def test_a_selector_that_reads_still_answers(site):
    """The check refuses only what IfcOpenShell cannot read or answers with nothing for a reason it can name."""
    for selector in ("IfcWall", "IfcWall, IfcDoor", 'IfcWall, Name = "Wall A"', 'IfcSlab, location = "GROUND"', "IfcFooting + IfcSlab"):
        refused, answer = call(site, "ifc_select", {"selector": selector})
        assert not refused, (selector, answer)
        assert answer["page"]["total"] > 0, selector


def test_the_selector_forms_are_written_where_a_chat_reads_first(site):
    """ifc_select's description and SKILL.md's "Find" step each give the forms the field guessed: contains, a regex,
    either of two classes, and the attribute capitalised; and every selector SKILL.md gives there reads."""
    tools = {t.name: t.description for t in anyio.run(site.list_tools)}
    find = re.search(r"2\. \*\*Find\.\*\*(.*?)\n3\. ", SKILL.read_text(), re.S)
    assert find, "SKILL.md has its Find step"
    for where, text in (("ifc_select's description", tools["ifc_select"]), ("SKILL.md's Find step", find.group(1))):
        for form in ('Name *= "', "= /", " + "):
            assert form in text, f"{where} does not give {form!r}"
        assert re.search(r"Name`+,\s+not\s+`+name", text), f"{where} does not say an attribute is capitalised"
    for selector in [s for s in re.findall(r"`([^`]+)`", find.group(1)) if s.startswith("Ifc")]:
        refused, answer = call(site, "ifc_select", {"selector": selector})
        assert not refused, (selector, answer)


# ------------------------------------------------------------------ arguments


def test_every_tool_refuses_an_argument_it_does_not_take_and_names_those_it_does(fresh):
    tools = {t.name: t.input_schema for t in anyio.run(fresh.list_tools)}
    for tool, schema in tools.items():
        refused, text = call(fresh, tool, {"no_such_argument": 1})
        assert refused, f"{tool} answered a call with an argument it does not take: {str(text)[:200]}"
        assert_teaches(text, "no_such_argument")
        for name in schema.get("properties", {}):
            assert name in said(text), f"{tool}'s refusal does not name its argument {name!r}: {said(text)}"


#: (tool, what the chat sent, the call it meant). The field's own guesses (flo2.io's call log, 2026-11-04 and 11-05).
GUESSED_ARGUMENTS = [
    ("ifc_select", {"where": "IfcWall"}, {"selector": "IfcWall"}),
    ("ifc_docs", {"function": "spatial.assign_container"}, {"function_path": "spatial.assign_container"}),
    ("ifc_extent", {"location": "GROUND"}, {"selector": 'IfcElement, location = "GROUND"'}),
    ("ifc_tree", {"depth": 2}, None),
    ("ifc_list", {"type": "IfcWall"}, None),
    ("ifc_edit", {"function": "root.create_entity", "params": "{}"}, {"function_path": "root.create_entity", "params": "{}"}),
]


@pytest.mark.parametrize("tool, sent, meant", GUESSED_ARGUMENTS, ids=[f"{t}-{next(iter(s))}" for t, s, _ in GUESSED_ARGUMENTS])
def test_a_guessed_argument_name_is_refused_with_the_call_it_meant(fresh, tool, sent, meant):
    before = edits_so_far(fresh)
    refused, text = call(fresh, tool, sent)
    assert refused, f"{tool} answered {sent}: {str(text)[:200]}"
    assert_teaches(text, *([json.dumps(meant)] if meant else []))
    assert edits_so_far(fresh) == before
    if meant:
        refused, answer = call(fresh, tool, meant)
        assert not refused, answer


def test_an_edit_functions_own_arguments_beside_params_are_refused_not_dropped(fresh):
    """The field sent root.create_entity's arguments beside `params`. Dropped, they made an IfcBuildingElementProxy
    with no name; refused, the refusal gives the call with them inside `params`, which makes the wall asked for."""
    sent = {"function_path": "root.create_entity", "ifc_class": "IfcWall", "name": "W9", "predefined_type": "STANDARD"}
    meant = {"function_path": "root.create_entity", "params": json.dumps({"ifc_class": "IfcWall", "name": "W9", "predefined_type": "STANDARD"})}
    before = edits_so_far(fresh)
    refused, text = call(fresh, "ifc_edit", sent)
    assert refused, f"ifc_edit made an edit with its function's arguments dropped: {text}"
    assert_teaches(text, json.dumps(meant))
    assert edits_so_far(fresh) == before, "a refused edit changed the model"
    refused, answer = call(fresh, "ifc_edit", meant)
    assert not refused and answer["ok"], answer
    refused, found = call(fresh, "ifc_select", {"selector": 'IfcWall, Name = "W9"'})
    assert found["page"]["total"] == 1, found


def test_a_missing_or_mistyped_argument_is_said_in_the_tools_own_words(site):
    refused, text = call(site, "ifc_docs", {})
    assert refused, text
    assert_teaches(text, "function_path")
    refused, text = call(site, "ifc_tree", {"limit": "many"})
    assert refused, text
    assert_teaches(text, "limit")
    assert "whole number" in said(text), text


# ------------------------------------------------------------------ editing functions


def test_an_edit_functions_misnamed_parameter_is_answered_with_the_ones_it_takes(fresh):
    """The field's spatial.assign_container with `product`: the function takes `products`, a list."""
    before = edits_so_far(fresh)
    refused, answer = call(fresh, "ifc_edit", {"function_path": "spatial.assign_container", "params": json.dumps({"product": "86", "relating_structure": "14"})})
    assert not refused and answer["ok"] is False, answer
    assert_teaches(answer["error"], "products", "relating_structure")
    assert "did you mean `products`" in answer["error"].lower(), answer["error"]
    assert edits_so_far(fresh) == before


def test_an_edit_function_or_module_that_does_not_exist_is_answered_with_what_there_is(site):
    refused, answer = call(site, "ifc_edit", {"function_path": "spatial.assign_containr", "params": "{}"})
    assert not refused and answer["ok"] is False, answer
    assert_teaches(answer["error"], "spatial.assign_container")
    refused, text = call(site, "ifc_list", {"module": "space"})
    assert refused, text
    assert_teaches(text, "spatial")
    refused, text = call(site, "ifc_docs", {"function_path": "spatial"})
    assert refused, text
    assert_teaches(text, "spatial.assign_container")

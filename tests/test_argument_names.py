"""One name for one thing across the tools, and every old name still taken (since 0.9.0).

A designer found one element named four ways: ``element_id`` (an integer) in ifc_info, ifc_relations and ifc_extent,
``a`` and ``b`` (a step id or GlobalId) in ifc_distance, ``ids`` in ifc_footprint and ifc_volume; and the selector
was ``query`` in ifc_select and ``selector`` everywhere else (flo2's design 1a97fc1f1de4d5d2,
fact:root-cause-a-helpers-describe-is-refused-because-the-client-holds-a-tool-list-without-it-2026-10-26). The rule
(server.py, ``ARGUMENT_NAMES``): the name most tools already used. These tests hold:

- no served tool names one of these things any other way (``SYNONYMS``), save the exceptions server.py gives reasons for;
- every old name (server.ALIASES) is taken and answered as its new name is, and is not served;
- an old and a new name given different values is refused, not silently picked between;
- one element is taken by its step id or its GlobalId wherever flo2-ifc reads it.
"""

from __future__ import annotations

import json

import anyio
import pytest

from flo2_ifc.server import ALIASES, ARGUMENT_NAMES, REPLY_NAMES, build_server, canonical_arguments, reply_names_taken
from tests import models

#: Names that would name one of ARGUMENT_NAMES's things another way.
SYNONYMS = {
    "element_id": {"a", "b", "id", "element", "elementid", "step_id", "global_id", "guid"},
    "element_ids": {"ids", "elements", "element_list", "global_ids", "guids"},
    "selector": {"query", "select", "filter"},
    "element_type": {"ifc_class", "class_name", "cls"},
}
#: Served names server.py keeps, with its reason ("Left as they are"): ifc_schema's class is not an element's.
KEPT = {("ifc_schema", "entity_type")}


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    path = tmp_path_factory.mktemp("names") / "site.ifc"
    site = models.measured_site()
    site.write(str(path))
    walls = {w.Name: (w.id(), w.GlobalId) for w in site.by_type("IfcWall")}
    server = build_server()

    async def run():
        await server.call_tool("ifc_load", {"path": str(path)})
        tools = {t.name: t.input_schema for t in await server.list_tools()}
        return tools

    return server, anyio.run(run), walls


def call(server, tool, arguments):
    from mcp.server.mcpserver.exceptions import ToolError

    async def run():
        try:
            result = await server.call_tool(tool, arguments)
        except ToolError as e:  # what a client receives as an is_error result
            return str(e)
        text = "".join(b.text for b in result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    return anyio.run(run)


def test_no_two_tools_name_one_thing_differently(served):
    _, tools, _ = served
    for tool, schema in tools.items():
        for arg in schema.get("properties", {}):
            for canonical, others in SYNONYMS.items():
                assert arg not in others, f"{tool} names {ARGUMENT_NAMES[canonical]!r} as {arg!r}, not {canonical!r}"
            if arg == "entity_type":
                assert (tool, arg) in KEPT, tool


def test_an_old_name_is_not_served(served):
    _, tools, _ = served
    for tool, renames in ALIASES.items():
        props = tools[tool]["properties"]
        for old, new in renames.items():
            assert old not in props and new in props, (tool, old, new)


def _strip(answer):
    """An answer without what says how it was asked (cite.elements_asked, seconds)."""
    if isinstance(answer, dict):
        return {k: _strip(v) for k, v in answer.items() if k not in ("seconds", "elements_asked", "asked")}
    return answer


#: Each alias, called the old way and the new way: (tool, old arguments, new arguments), from Wall A's and Wall B's ids.
CALLS = [
    ("ifc_select", lambda a, b, b_guid: {"query": "IfcWall"}, lambda a, b, b_guid: {"selector": "IfcWall"}),
    ("ifc_footprint", lambda a, b, b_guid: {"ids": f"{a}, {b}"}, lambda a, b, b_guid: {"element_ids": f"{a}, {b}"}),
    ("ifc_volume", lambda a, b, b_guid: {"ids": str(a)}, lambda a, b, b_guid: {"element_ids": [a]}),
    ("ifc_distance", lambda a, b, b_guid: {"a": str(a), "b": b_guid}, lambda a, b, b_guid: {"element_id": a, "other_element_id": b_guid}),
]


@pytest.mark.parametrize(("tool", "old", "new"), CALLS, ids=[c[0] for c in CALLS])
def test_every_old_name_is_answered_as_its_new_one(served, tool, old, new):
    server, _, walls = served
    (a, _), (b, b_guid) = walls["Wall A"], walls["Wall B"]
    by_old, by_new = call(server, tool, old(a, b, b_guid)), call(server, tool, new(a, b, b_guid))
    assert isinstance(by_old, dict), by_old
    assert _strip(by_old) == _strip(by_new)


def test_every_alias_is_covered_above():
    assert set(ALIASES) == {tool for tool, _, _ in CALLS}


def test_an_old_and_a_new_name_that_disagree_are_refused(served):
    with pytest.raises(ValueError, match="give `selector` only"):
        canonical_arguments("ifc_select", {"query": "IfcWall", "selector": "IfcSlab"})
    assert canonical_arguments("ifc_select", {"query": "IfcWall", "selector": "IfcWall"}) == {"selector": "IfcWall"}
    server, _, _ = served

    async def run():
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError, match="give `element_ids` only"):
            await server.call_tool("ifc_footprint", {"ids": "1", "element_ids": "2"})

    anyio.run(run)


def test_one_element_is_named_by_its_step_id_or_its_global_id(served):
    server, _, walls = served
    a, a_guid = walls["Wall A"]
    by_id, by_guid = call(server, "ifc_info", {"element_id": a}), call(server, "ifc_info", {"element_id": a_guid})
    assert by_id["id"] == by_guid["id"] == a
    assert call(server, "ifc_relations", {"element_id": a_guid})
    assert call(server, "ifc_extent", {"element_id": a_guid})["overall"] == call(server, "ifc_extent", {"element_id": a})["overall"]
    assert "No element has the id or GlobalId" in call(server, "ifc_info", {"element_id": "0zzzzzzzzzzzzzzzzzzzzz"})


# ── A name a reply gives an element is a name the tools take (since 0.9.1) ────────────────────────────────────────
#
# 0.9.0 gave each thing one name in what the tools TAKE; their replies still name an element `id` (its step id) and
# `global_id`. A chat on flo2.io read {"id": 157952} off ifc_select and was refused ifc_info {"id": 157952}
# (1510fc3728665dbf, fact:root-cause-a-reply-names-an-element-id-while-the-tools-take-element-id-2026-10-31). So every
# name a reply gives one element is taken wherever one element is (`element_id`), and these tests hold the round trip.

#: The names a reply may give one element (server.REPLY_NAMES, and `element_id` itself); anything else naming one is a
#: name the tools do not take.
ACCEPTED_FOR_ONE_ELEMENT = {"element_id", *REPLY_NAMES}


def _one_element_tools(tools):
    return sorted(t for t, schema in tools.items() if "element_id" in schema.get("properties", {}))


def test_every_name_a_reply_gives_an_element_is_taken_wherever_one_element_is(served):
    server, tools, walls = served
    a, a_guid = walls["Wall A"]
    one = _one_element_tools(tools)
    assert {"ifc_info", "ifc_relations", "ifc_extent", "ifc_distance"} <= set(one), one
    from mcp.server.mcpserver.exceptions import ToolError

    async def disagree(tool, name):
        # Taken as `element_id` before the arguments are checked: shown by the refusal of two names that disagree,
        # which needs none of the tool's other arguments.
        with pytest.raises(ToolError, match="give `element_id` only"):
            await server.call_tool(tool, {name: a, "element_id": a + 1})

    assert set(REPLY_NAMES) == {"id", "global_id"}, REPLY_NAMES
    for tool in one:
        names = reply_names_taken(tools[tool])
        assert "id" in names, tool
        for name in names:
            anyio.run(disagree, tool, name)
    # A GlobalId goes only where `element_id` takes one: ifcmcp's ifc_clash takes the step id alone.
    assert reply_names_taken(tools["ifc_clash"]) == ("id",)
    assert reply_names_taken(tools["ifc_info"]) == REPLY_NAMES
    for tool in ("ifc_info", "ifc_relations", "ifc_extent"):
        by_name = _strip(call(server, tool, {"element_id": a}))
        assert _strip(call(server, tool, {"id": a})) == by_name, tool
        assert _strip(call(server, tool, {"global_id": a_guid})) == _strip(call(server, tool, {"element_id": a_guid})), tool


def _element_like(node):
    kind = node.get("type", node.get("class"))
    return isinstance(kind, str) and kind.startswith("Ifc")


def _names_for_elements(answer, model):
    """Every key that holds an element's step id or GlobalId, in a dict that describes an element."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            if _element_like(node):
                for key, value in node.items():
                    if isinstance(value, bool):
                        continue
                    if isinstance(value, int):
                        try:
                            if model.by_id(value).is_a() == node.get("type", node.get("class")):
                                found.add(key)
                        except RuntimeError:
                            pass
                    elif isinstance(value, str) and len(value) == 22:
                        try:
                            model.by_guid(value)
                            found.add(key)
                        except RuntimeError:
                            pass
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(answer)
    return found


def test_a_reply_names_an_element_only_by_names_the_tools_take(served, tmp_path):
    import ifcopenshell

    server, _, walls = served
    a, _ = walls["Wall A"]
    path = tmp_path / "site.ifc"
    models.measured_site().write(str(path))
    model = ifcopenshell.open(str(path))
    answers = {
        "ifc_select": call(server, "ifc_select", {"selector": "IfcWall"}),
        "ifc_tree": call(server, "ifc_tree", {}),
        "ifc_info": call(server, "ifc_info", {"element_id": a}),
        "ifc_relations": call(server, "ifc_relations", {"element_id": a}),
    }
    seen = set()
    for tool, answer in answers.items():
        assert isinstance(answer, dict), (tool, answer)
        names = _names_for_elements(answer, model)
        assert names <= ACCEPTED_FOR_ONE_ELEMENT, f"{tool} names an element {sorted(names - ACCEPTED_FOR_ONE_ELEMENT)}, which no tool takes"
        seen |= names
    assert "id" in seen, "the walk found no element named in any reply: it would pass for the wrong reason"

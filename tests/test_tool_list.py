"""The tools a chat sees did not change when ifcmcp came from upstream's release instead of the fork.

flo2-ifc 0.3.1 installed ifcmcp from Anthony's fork of IfcOpenShell (commit
0ea3edc7a), whose own server ran on the MCP Python SDK 2.x. Since 0.4.0 the
tool code comes from IfcOpenShell's published ifcopenshell-mcp, and flo2-ifc's
own server registers every tool. tests/tools-0.3.1.json is what 0.3.1 listed
over stdio; this test asks the installed server (or FLO2_IFC_SERVER) the same
and compares:

- the server's name and instructions;
- every tool, in order, by name;
- each tool's input schema and output schema, exactly;
- each tool's description (by sha256, so ifcmcp's LGPL text is not copied here);
- the reason each failing ifcmcp tool gives, which mcp 2.x hides unless the
  server raises it as a ToolError.

A difference here means the tools changed. If that is meant, for example on a
new ifcopenshell-mcp, flo2's real-ifcmcp door test must be changed with it.

Since 0.5.0 three tools are ADDED after 0.3.1's (``NEW_SINCE``): the IDS check,
the IDS writer and buildingSMART's rules; since 0.6.0 four more, the building
steps (a wall, a slab, a roof and a room, each in one call); since 0.9.0 one
more, ``ifc_add_unit``. Every tool 0.3.1 listed is still listed, in the same
order, and nothing else is new.

Since 0.9.0 seven of flo2-ifc's tools name their arguments by one rule
(server.py, ``ARGUMENT_NAMES``) and their descriptions say so, and ifc_summary,
ifc_units and ifc_info say every unit (``RENAMED``, ``REDESCRIBED``). Their
schemas are not 0.3.1's any more; instead every argument 0.3.1 took is still
taken, under its own name or as an alias of its new one, with the same type
accepted and the same arguments required.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from flo2_ifc.server import ALIASES
from tests.test_smoke import flo2_ifc_command

BEFORE = json.loads((Path(__file__).parent / "tools-0.3.1.json").read_text())
#: The tools added since 0.3.1, in the order they are listed after its tools.
NEW_SINCE = ["ifc_check_ids", "ifc_write_ids", "ifc_check_rules", "ifc_add_wall", "ifc_add_slab", "ifc_add_roof", "ifc_add_space", "ifc_add_unit"]
#: Since 0.9.0: tools whose arguments were renamed (ALIASES) or now also take a GlobalId (element_id).
RENAMED = ["ifc_select", "ifc_info", "ifc_relations", "ifc_extent", "ifc_footprint", "ifc_volume", "ifc_distance"]
#: Since 0.9.0: tools whose descriptions changed: those renamed, and the three that say every unit.
REDESCRIBED = [*RENAMED, "ifc_summary", "ifc_units"]

#: Calls that fail, each keyed as tools-0.3.1.json keys its error. The first
#: group runs with no model open, the second after ifc_new.
FAILING = [
    ("ifc_save", "ifc_save", {}),
    ("ifc_clash", "ifc_clash", {"element_id": 1}),
    ("ifc_materials", "ifc_materials", {}),
    ("ifc_load", "ifc_load", {"path": "/nonexistent/missing.ifc"}),
    ("ifc_docs", "ifc_docs", {"function_path": "no.such_function"}),
    ("ifc_schema", "ifc_schema", {"entity_type": "IfcNoSuchThing"}),
    ("ifc_shape_docs", "ifc_shape_docs", {"method": "no_such_method"}),
    ("ifc_edit", "ifc_edit", {"function_path": "root.create_entity", "params": "{not json"}),
]
FAILING_WITH_A_MODEL = [
    ("ifc_clash (model open)", "ifc_clash", {"element_id": 999999}),
    ("ifc_edit (model open)", "ifc_edit", {"function_path": "root.create_entity", "params": "{not json"}),
]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def listing() -> dict:
    override = shlex.split(os.environ.get("FLO2_IFC_SERVER", ""))
    command, args = (override[0], override[1:]) if override else (flo2_ifc_command(), [])
    out: dict = {"errors": {}}
    with anyio.fail_after(120):
        async with stdio_client(StdioServerParameters(command=command, args=args)) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            out["server"] = {"name": init.server_info.name, "instructions_sha256": sha256(init.instructions or "")}
            tools = (await session.list_tools()).tools
            out["tools"] = [
                {
                    "name": t.name,
                    "description_sha256": sha256(t.description or ""),
                    "inputSchema": t.input_schema,
                    "outputSchema": t.output_schema,
                }
                for t in tools
            ]
            for key, name, arguments in FAILING:
                result = await session.call_tool(name, arguments)
                out["errors"][key] = (result.is_error, "".join(getattr(b, "text", "") for b in result.content))
            await session.call_tool("ifc_new", {"schema": "IFC4"})
            for key, name, arguments in FAILING_WITH_A_MODEL:
                result = await session.call_tool(name, arguments)
                out["errors"][key] = (result.is_error, "".join(getattr(b, "text", "") for b in result.content))
    return out


@pytest.fixture(scope="module")
def now() -> dict:
    return anyio.run(listing)


def test_the_server_keeps_its_name_and_instructions(now):
    assert now["server"] == BEFORE["server"]


def _old(now) -> list[dict]:
    return [t for t in now["tools"] if t["name"] not in NEW_SINCE]


def test_the_same_tools_in_the_same_order(now):
    assert [t["name"] for t in _old(now)] == [t["name"] for t in BEFORE["tools"]]


def test_only_the_new_tools_are_added(now):
    names = [t["name"] for t in now["tools"]]
    added = [n for n in names if n not in {t["name"] for t in BEFORE["tools"]}]
    assert sorted(added) == sorted(NEW_SINCE), added


def test_every_tool_takes_the_same_arguments(now):
    before = {t["name"]: t["inputSchema"] for t in BEFORE["tools"]}
    for tool in _old(now):
        if tool["name"] not in RENAMED:
            assert tool["inputSchema"] == before[tool["name"]], tool["name"]


def _types(prop: dict) -> set[str]:
    return {prop["type"]} if "type" in prop else {t.get("type") for t in prop.get("anyOf", [])}


def test_every_argument_a_renamed_tool_took_is_still_taken(now):
    """A caller holding 0.3.1's tool list still calls the same way: each old argument is served under its own name or
    taken as an alias of its new one (server.ALIASES), its old type is still accepted, and what was required still is."""
    before = {t["name"]: t["inputSchema"] for t in BEFORE["tools"]}
    for tool in [t for t in _old(now) if t["name"] in RENAMED]:
        name, old, new = tool["name"], before[tool["name"]], tool["inputSchema"]
        renamed = ALIASES.get(name, {})
        for arg, prop in old["properties"].items():
            served = renamed.get(arg, arg)
            assert served in new["properties"], (name, arg)
            assert _types(prop) <= _types(new["properties"][served]), (name, arg)
        assert {renamed.get(a, a) for a in old.get("required", [])} == set(new.get("required", [])), name
        assert not set(renamed) & set(new["properties"]), (name, "an old name is still served")


def test_every_tool_answers_in_the_same_shape(now):
    before = {t["name"]: t["outputSchema"] for t in BEFORE["tools"]}
    for tool in _old(now):
        assert tool["outputSchema"] == before[tool["name"]], tool["name"]


def test_every_tool_keeps_its_description(now):
    before = {t["name"]: t["description_sha256"] for t in BEFORE["tools"]}
    for tool in _old(now):
        if tool["name"] not in REDESCRIBED:
            assert tool["description_sha256"] == before[tool["name"]], tool["name"]


@pytest.mark.parametrize("key", [k for k, _, _ in FAILING + FAILING_WITH_A_MODEL])
def test_a_failing_ifcmcp_tool_gives_the_same_reason(now, key):
    is_error, text = now["errors"][key]
    assert is_error, key
    assert text == BEFORE["errors"][key]
    # The reason itself, not only mcp 2.x's bare "Error executing tool <name>".
    assert text.split(": ", 1)[1:], text

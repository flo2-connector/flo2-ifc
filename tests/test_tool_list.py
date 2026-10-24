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

from tests.test_smoke import flo2_ifc_command

BEFORE = json.loads((Path(__file__).parent / "tools-0.3.1.json").read_text())

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


def test_the_same_tools_in_the_same_order(now):
    assert [t["name"] for t in now["tools"]] == [t["name"] for t in BEFORE["tools"]]


def test_every_tool_takes_the_same_arguments(now):
    before = {t["name"]: t["inputSchema"] for t in BEFORE["tools"]}
    for tool in now["tools"]:
        assert tool["inputSchema"] == before[tool["name"]], tool["name"]


def test_every_tool_answers_in_the_same_shape(now):
    before = {t["name"]: t["outputSchema"] for t in BEFORE["tools"]}
    for tool in now["tools"]:
        assert tool["outputSchema"] == before[tool["name"]], tool["name"]


def test_every_tool_keeps_its_description(now):
    before = {t["name"]: t["description_sha256"] for t in BEFORE["tools"]}
    for tool in now["tools"]:
        assert tool["description_sha256"] == before[tool["name"]], tool["name"]


@pytest.mark.parametrize("key", [k for k, _, _ in FAILING + FAILING_WITH_A_MODEL])
def test_a_failing_ifcmcp_tool_gives_the_same_reason(now, key):
    is_error, text = now["errors"][key]
    assert is_error, key
    assert text == BEFORE["errors"][key]
    # The reason itself, not only mcp 2.x's bare "Error executing tool <name>".
    assert text.split(": ", 1)[1:], text

"""flo2-ifc: ifcmcp as flo2.io runs it.

The tools are IfcOpenShell's ifcmcp, from the ifcopenshell-mcp release
pyproject.toml pins, served by flo2-ifc's own server on the MCP Python SDK 2.x,
with flo2-ifc's readers for a whole building in front of seven of them and six
tools added (server.py, reading.py, geometry.py, drawing.py). ``flo2-ifc`` runs
it over stdio, and ``uvx --from git+https://github.com/flo2-connector/flo2-ifc
flo2-ifc`` runs it with every dependency at the version pinned in
pyproject.toml. The ``ifcmcp`` command the release also installs is upstream's
own server, which needs mcp<2, so it does not start here.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys

__version__ = "0.4.0"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="flo2-ifc",
        description="ifcmcp, IfcOpenShell's MCP server for IFC building models, as flo2.io runs it.",
    )
    parser.add_argument("--version", action="version", version=f"flo2-ifc {__version__} (ifcmcp from ifcopenshell-mcp, pinned in pyproject.toml, on mcp 2.x)")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="MCP transport (default: stdio)")
    args = parser.parse_args()
    if importlib.util.find_spec("mcp.server.mcpserver") is None:
        print("error: the 'mcp' package is required to run the server.", file=sys.stderr)
        sys.exit(1)
    from flo2_ifc.server import build_server

    build_server().run(transport=args.transport)

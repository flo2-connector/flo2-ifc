"""flo2-ifc: the exact ifcmcp set flo2.io runs.

This package holds no server code of its own. ``main`` hands off to ifcmcp's
own command line, so ``flo2-ifc`` and ``ifcmcp`` are the same server, and
``uvx --from git+https://github.com/flo2-connector/flo2-ifc flo2-ifc`` runs it with
every dependency at the version pinned in pyproject.toml.
"""

__version__ = "0.1.0"


def main() -> None:
    from ifcmcp.__main__ import main as ifcmcp_main

    ifcmcp_main()

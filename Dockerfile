# IfcOpenShell's ifcmcp, as the image flo2-tool-sandbox runs one container of
# per person's session on a design (dec:idea-a-private-worker-droplet-runs-
# tool-servers-like-ifcmcp, settled 2026-10-02). flo2 builds it from this file
# at a pinned commit of this repository, as it builds flo2-cad's.
#   docker build -t flo2-ifc .
#   docker run --rm -i --network none --read-only --user 65534:65534 flo2-ifc
#
# PINNED, IN ONE PLACE. The image installs this package and nothing else, so
# the set it runs is exactly pyproject.toml's: ifcmcp from Anthony's fork at
# one commit (ported to the MCP Python SDK 2.x; every ifcopenshell-mcp on PyPI
# so far requires mcp<2), mcp 2.3.0, and the helpers at 0.9.0. ifcmcp leaves
# its helpers unpinned, and they decide what a model can measure: with ifc5d
# 0.9.0 the base-quantities rule measures a room modelled as a plain box, with
# 0.8.5 it silently skips it (measured on the droplet, 2026-10-02). networkx
# is pinned because without it `ifcedit list` prints a note to stdout ahead of
# its JSON. No pyvista (900 MB; only ifc_render, which flo2's door does not
# offer, needs it).
#
# TWO STAGES. The fork pin is a git URL, so installing needs git; the build
# stage has it and the image does not. The image gets only the finished
# environment, copied to the same path on the same base.
FROM python:3.12-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m venv /opt/flo2-ifc \
 && /opt/flo2-ifc/bin/pip install --no-cache-dir --disable-pip-version-check . \
 && /opt/flo2-ifc/bin/pip freeze --all

# The image does nothing to confine itself. flo2-tool-sandbox runs it with no
# network, a read-only root, an unprivileged user (65534), a memory cap, and
# only the design's folder mounted, read-only, at /design. So nothing here
# may need the network or a writable root at run time: HOME is /tmp (a tmpfs
# or nothing), and no bytecode is written.
FROM python:3.12-slim
COPY --from=build /opt/flo2-ifc /opt/flo2-ifc
ENV PATH=/opt/flo2-ifc/bin:$PATH HOME=/tmp PYTHONDONTWRITEBYTECODE=1
RUN ifcmcp --version
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["ifcmcp"]

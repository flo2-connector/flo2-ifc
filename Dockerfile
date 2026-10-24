# IfcOpenShell's ifcmcp with flo2-ifc's readers for a whole building
# (src/flo2_ifc), as the image flo2-tool-sandbox runs one container of
# per person's session on a design (dec:idea-a-private-worker-droplet-runs-
# tool-servers-like-ifcmcp, settled 2026-10-02). flo2 builds it from this file
# at a pinned commit of this repository, as it builds flo2-cad's.
#   docker build -t flo2-ifc .
#   docker run --rm -i --network none --read-only --user 65534:65534 flo2-ifc
#
# PINNED, IN ONE PLACE. The image installs this package and nothing else, so
# the set it runs is exactly pyproject.toml's, every one a release on PyPI:
# ifcmcp's tool code from ifcopenshell-mcp 0.9.0 without its [mcp] extra
# (which requires mcp<2), served by flo2-ifc's own server on mcp 2.3.0, and
# the helpers at 0.9.0. ifcmcp leaves
# its helpers unpinned, and they decide what a model can measure: with ifc5d
# 0.9.0 the base-quantities rule measures a room modelled as a plain box, with
# 0.8.5 it silently skips it (measured on the droplet, 2026-10-02). networkx
# is pinned because without it `ifcedit list` prints a note to stdout ahead of
# its JSON. No pyvista (900 MB; only ifc_render, which flo2's door does not
# offer, needs it).
#
# TWO STAGES. The build stage installs the package from its source; the image
# gets only the finished environment, copied to the same path on the same
# base, and not the source. (Since 0.4.0 nothing is installed from git, so
# neither stage needs git.)
FROM python:3.12-slim AS build
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
# ifc_draw renders its SVG to PNG with cairosvg, which loads the cairo
# library at run time (cairocffi), and writes its words in DejaVu Sans. Both
# come from Debian, installed here and nowhere else; fontconfig's cache is
# built at install, so nothing is written to the read-only root at run time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcairo2 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/flo2-ifc /opt/flo2-ifc
ENV PATH=/opt/flo2-ifc/bin:$PATH HOME=/tmp PYTHONDONTWRITEBYTECODE=1
# The entry point is flo2-ifc: its own server on mcp 2.x with every one of
# ifcmcp's tools, the readers in front of seven of them and two added (since
# flo2-ifc 0.2.0), and the measures and the drawing (since 0.3.0). `ifcmcp`,
# also on PATH, is upstream's own server, which needs mcp<2: here it answers
# --version and does not serve.
RUN flo2-ifc --version && ifcmcp --version \
 && python -c "import cairosvg; assert cairosvg.svg2png(bytestring=b'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"4\" height=\"4\"><text y=\"3\">a</text></svg>')[:4] == b'\x89PNG'"
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["flo2-ifc"]

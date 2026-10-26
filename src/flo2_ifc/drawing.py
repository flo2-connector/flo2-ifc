"""Drawing a building model to scale: a plan, a section or an elevation, as SVG and PNG.

ifcmcp's own ``ifc_plot`` draws through ``ifcopenshell.draw``, which in
ifcopenshell 0.9.0 raises in its cells pass (draw.py:376: ``'SwigPyObject'
object has no attribute 'is_a'``) and, with cells off, draws no scale, no
dimensions and nothing on a storey with no elevation. So flo2-ifc draws for
itself, from the same triangle meshes the measures use (geometry.py):

- a PLAN at a storey cuts every element of it with a level plane at the cut
  height: what the plane cuts is drawn dark (poché), what lies below it as its
  outline seen from above, nearest on top, and each room's outline and name;
- a SECTION cuts the model with an upright plane along a line (two points, a
  grid line, or through the middle of named elements), and draws what it cuts
  dark and what lies beyond as seen, nearest on top, with each storey's level;
- an ELEVATION draws the model seen from one side, nearest on top.

Each is drawn to a stated scale on a stated sheet, with the scale, the model's
unit, a scale bar in metres and in feet, and the model file it was drawn from
written on it. A set of elements can be highlighted, so a later drawing can
show what changed. The SVG is in millimetres of paper, so it prints at its
scale; the PNG is rendered from it with cairosvg. Reads only.
"""

from __future__ import annotations

import math
import os
import re
import time
from fractions import Fraction
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import numpy as np

from flo2_ifc import geometry, reading

# ---------------------------------------------------------------- sheets and scales

#: Sheet sizes, long side first, in millimetres.
PAPERS: dict[str, tuple[float, float, str]] = {
    "a4": (297.0, 210.0, "A4"),
    "a3": (420.0, 297.0, "A3"),
    "a2": (594.0, 420.0, "A2"),
    "a1": (841.0, 594.0, "A1"),
    "a0": (1189.0, 841.0, "A0"),
    "letter": (279.4, 215.9, 'Letter (11 x 8 1/2")'),
    "tabloid": (431.8, 279.4, 'Tabloid (17 x 11")'),
    "arch c": (609.6, 457.2, 'ARCH C (24 x 18")'),
    "arch d": (914.4, 609.6, 'ARCH D (36 x 24")'),
    "ansi d": (863.6, 558.8, 'ANSI D (34 x 22")'),
}
PAPER_ALIASES = {"ansi b": "tabloid", "11x17": "tabloid", "ledger": "tabloid", "ansi a": "letter", "24x36": "arch d", "18x24": "arch c"}
DEFAULT_PAPER = "tabloid"
MARGIN_MM = 10.0
TITLE_MM = 24.0

#: The architect's scales an imperial sheet uses, by their ratio.
IMPERIAL = {
    12: '1" = 1\'-0"',
    16: '3/4" = 1\'-0"',
    24: '1/2" = 1\'-0"',
    32: '3/8" = 1\'-0"',
    48: '1/4" = 1\'-0"',
    64: '3/16" = 1\'-0"',
    96: '1/8" = 1\'-0"',
    128: '3/32" = 1\'-0"',
    192: '1/16" = 1\'-0"',
    384: '1/32" = 1\'-0"',
    120: "1\" = 10'",
    240: "1\" = 20'",
    360: "1\" = 30'",
    480: "1\" = 40'",
    600: "1\" = 50'",
}
#: The scales a drawing is fitted to when none is given, largest first: the
#: architect's and the metric ones together.
CANDIDATES = (10, 12, 16, 20, 24, 25, 32, 48, 50, 64, 96, 100, 128, 192, 200, 250, 384, 500, 1000, 2000, 5000)


def scale_say(ratio: int) -> str:
    return f"1:{ratio}" + (f" ({IMPERIAL[ratio]})" if ratio in IMPERIAL else "")


def parse_scale(text: str) -> int:
    """A scale as a ratio: "1:100", "100", '1/8" = 1\'-0"' (1:96), or '1" = 20\'' (1:240)."""
    t = text.strip().replace("″", '"').replace("′", "'").replace("''", '"')
    m = re.fullmatch(r"(?:1\s*:\s*)?(\d+(?:\.\d+)?)", t)
    if m:
        r = float(m.group(1))
    else:
        m = re.fullmatch(r"(\d+(?:\s+\d+)?(?:/\d+)?|\d*\.\d+)\s*\"\s*=\s*(\d+(?:\.\d+)?)\s*'(?:\s*-?\s*0\s*\"?)?", t)
        if not m:
            raise ValueError(f'{text!r} is not a scale: give it as "1:100" or as an architect\'s scale such as 1/8" = 1\'-0" (that is 1:96).')
        inches = float(sum(Fraction(p) for p in m.group(1).split()))
        r = float(m.group(2)) * 12 / inches
    if r < 1 or r > 100000 or abs(r - round(r)) > 1e-6:
        raise ValueError(f"{text!r} gives a scale of 1:{r:g}; a drawing's scale is 1:N for a whole N from 1 to 100,000.")
    return int(round(r))


def paper_of(text: str) -> tuple[float, float, str]:
    key = (text or DEFAULT_PAPER).strip().lower().replace("-", " ").replace("_", " ")
    key = PAPER_ALIASES.get(key, key)
    if key not in PAPERS:
        raise ValueError(f"{text!r} is not a sheet this draws on: {', '.join(v[2] for v in PAPERS.values())}.")
    return PAPERS[key]


# ---------------------------------------------------------------- naming elements


def elements_named(model: ifcopenshell.file, text: str) -> list[ifcopenshell.entity_instance]:
    """Elements by step ids or GlobalIds (comma separated), or else by an IfcOpenShell selector."""
    text = text.strip()
    if not text:
        return []
    tokens = [t for t in text.replace(",", " ").split() if t]
    if all(re.fullmatch(r"#?\d+", t) or re.fullmatch(r"[0-9A-Za-z_$]{22}", t) for t in tokens):
        try:
            return geometry.choose(model, ids=text)[0]
        except ValueError:
            if not all(re.fullmatch(r"#?\d+", t) for t in tokens):
                pass  # 22 letters may be a class or a name: try it as a selector
            else:
                raise
    return geometry.choose(model, selector=text)[0]


def _drawable(e: ifcopenshell.entity_instance) -> bool:
    return e.is_a("IfcElement") and not e.is_a("IfcFeatureElement") and not e.is_a("IfcVirtualElement")


def storey_elements(storey: ifcopenshell.entity_instance) -> tuple[list[ifcopenshell.entity_instance], list[ifcopenshell.entity_instance]]:
    """A storey's elements and its rooms (spaces): what it contains, with their parts, and what is REFERENCED into it
    (reading.referenced_into), with those parts of each that belong to no other storey. So a plan of an upper storey
    cuts the wall rising through it from the storey below, and leaves out that wall's door on the storey below."""
    parts = set(ifcopenshell.util.element.get_decomposition(storey))
    for e in reading.referenced_into(storey):
        parts.add(e)
        for part in ifcopenshell.util.element.get_decomposition(e):
            container = ifcopenshell.util.element.get_container(part)
            if container is None or container == storey:
                parts.add(part)
    elements = sorted({e.id(): e for e in parts if _drawable(e)}.values(), key=lambda e: e.id())
    spaces = sorted({e.id(): e for e in parts if e.is_a("IfcSpace")}.values(), key=lambda e: e.id())
    return elements, spaces


def storeys_named(model: ifcopenshell.file, text: str) -> list[ifcopenshell.entity_instance]:
    if not text.strip():
        return list(model.by_type("IfcBuildingStorey"))
    return [reading.find_storey(model, t) for t in [p for p in text.split(",") if p.strip()]]


def storey_z_m(model: ifcopenshell.file, storey: ifcopenshell.entity_instance) -> float:
    """A storey's height in world coordinates, metres: its placement, or its Elevation."""
    z = reading._storey_z(storey)
    if z is None:
        z = getattr(storey, "Elevation", None) or 0.0
    return float(z) * reading.unit_scale(model)


# ---------------------------------------------------------------- cutting triangles


def _roll(tri: np.ndarray, first: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each triangle's corners turned (keeping their order) so corner `first` comes first."""
    idx = (first[:, None] + np.arange(3)[None, :]) % 3
    return np.take_along_axis(tri, idx[:, :, None], axis=1), np.take_along_axis(s, idx, axis=1)


def clip_keep(tri: np.ndarray, s: np.ndarray) -> np.ndarray:
    """The parts of triangles (m, 3, k) where s <= 0, as triangles; s (m, 3) is a signed distance per corner."""
    inside = s <= 0
    n = inside.sum(axis=1)
    out = [tri[n == 3]]
    one = n == 1
    if one.any():
        t, d = _roll(tri[one], np.argmax(inside[one], axis=1), s[one])
        a = t[:, 0] + (t[:, 1] - t[:, 0]) * (d[:, 0] / (d[:, 0] - d[:, 1]))[:, None]
        b = t[:, 0] + (t[:, 2] - t[:, 0]) * (d[:, 0] / (d[:, 0] - d[:, 2]))[:, None]
        out.append(np.stack([t[:, 0], a, b], axis=1))
    two = n == 2
    if two.any():
        t, d = _roll(tri[two], np.argmin(inside[two], axis=1), s[two])  # the corner outside first
        p20 = t[:, 2] + (t[:, 0] - t[:, 2]) * (d[:, 2] / (d[:, 2] - d[:, 0]))[:, None]
        p01 = t[:, 1] + (t[:, 0] - t[:, 1]) * (d[:, 1] / (d[:, 1] - d[:, 0]))[:, None]
        out.append(np.stack([t[:, 1], t[:, 2], p20], axis=1))
        out.append(np.stack([t[:, 1], p20, p01], axis=1))
    return np.concatenate(out) if out else tri[:0]


def cut_segments(tri: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Where a plane (s = 0) crosses triangles: segments (k, 2, dims)."""
    below = s < 0
    n = below.sum(axis=1)
    cross = (n == 1) | (n == 2)
    if not cross.any():
        return tri[:0, :2]
    t, d, b = tri[cross], s[cross], below[cross]
    odd = np.where(n[cross] == 1, np.argmax(b, axis=1), np.argmin(b, axis=1))
    t, d = _roll(t, odd, d)
    p = t[:, 0] + (t[:, 1] - t[:, 0]) * (d[:, 0] / (d[:, 0] - d[:, 1]))[:, None]
    q = t[:, 0] + (t[:, 2] - t[:, 0]) * (d[:, 0] / (d[:, 0] - d[:, 2]))[:, None]
    return np.stack([p, q], axis=1)


def silhouette(tri2: np.ndarray, grid: float, front_only: bool = False):
    """The union of 2D triangles (m, 3, 2), snapped to `grid` metres first.

    Snapping to a tenth of a millimetre of PAPER loses nothing the sheet can
    show and joins a detailed fixture's thousands of triangles into few: a large
    basement's outline joins in 3.5 s instead of 24 s (2026-10-24).
    The snapped corners are already on the grid, so the union itself is not
    snapped again (a quarter faster). `front_only` keeps only the triangles
    wound one way: enough for a CLOSED mesh seen whole, whose outline the
    triangles facing either way cover, and half the work; never for an open
    or a cut one."""
    import shapely

    if len(tri2) == 0:
        return shapely.Polygon()
    q = np.round(tri2 / grid)
    a = (q[:, 1, 0] - q[:, 0, 0]) * (q[:, 2, 1] - q[:, 0, 1]) - (q[:, 2, 0] - q[:, 0, 0]) * (q[:, 1, 1] - q[:, 0, 1])
    if front_only:
        q = q[a > 0]
    else:
        q = q[a != 0]
        flip = a[a != 0] < 0
        q[flip] = q[flip][:, ::-1]
    if len(q) == 0:
        return shapely.Polygon()
    q = np.unique(q.reshape(len(q), -1), axis=0).reshape(-1, 3, 2)
    out = shapely.union_all(shapely.polygons(q * grid))
    return out if out.is_valid else shapely.make_valid(out)


def cut_lines(seg2: np.ndarray, grid: float):
    """Cut segments (k, 2, 2) joined into lines: closed rings where the mesh is closed."""
    import shapely

    if len(seg2) == 0:
        return []
    q = np.round(seg2 / grid) * grid
    keep = np.any(q[:, 0] != q[:, 1], axis=1)
    q = q[keep]
    if len(q) == 0:
        return []
    merged = shapely.line_merge(shapely.union_all(shapely.linestrings(q), grid_size=grid))
    return [g for g in getattr(merged, "geoms", [merged]) if not g.is_empty]


# ---------------------------------------------------------------- the views


def _compass(word: str) -> np.ndarray:
    w = word.strip().lower()
    table = {"north": (0.0, 1.0), "south": (0.0, -1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0)}
    if w not in table:
        raise ValueError(f"{word!r} is not a side: north, south, east or west (north is the model's +y, east its +x).")
    return np.array(table[w] + (0.0,))


def _true_north(model: ifcopenshell.file) -> tuple[float, float] | None:
    for c in model.by_type("IfcGeometricRepresentationContext"):
        if c.is_a("IfcGeometricRepresentationSubContext"):
            continue
        tn = getattr(c, "TrueNorth", None)
        if tn is not None and c.ContextType == "Model":
            r = tn.DirectionRatios
            return (float(r[0]), float(r[1]))
    return None


def grid_line(model: ifcopenshell.file, tag: str) -> tuple[np.ndarray, np.ndarray]:
    """A grid axis's two ends in world coordinates, metres, by its tag (e.g. "A" or "3")."""
    scale = reading.unit_scale(model)
    tags = []
    for grid in model.by_type("IfcGrid"):
        matrix = ifcopenshell.util.placement.get_local_placement(grid.ObjectPlacement) if grid.ObjectPlacement else np.eye(4)
        for axes in (grid.UAxes or (), grid.VAxes or (), getattr(grid, "WAxes", None) or ()):
            for axis in axes:
                tags.append(axis.AxisTag)
                if (axis.AxisTag or "").strip().lower() != tag.strip().lower():
                    continue
                curve = axis.AxisCurve
                if not curve.is_a("IfcPolyline"):
                    raise ValueError(f"Grid line {tag!r} is a {curve.is_a()}; only a straight polyline grid line can be drawn along.")
                pts = [list(p.Coordinates) + [0.0] * (3 - len(p.Coordinates)) for p in (curve.Points[0], curve.Points[-1])]
                ends = [(np.asarray(matrix) @ np.array(p + [1.0]))[:3] * scale for p in pts]
                return ends[0], ends[1]
    if not tags:
        raise ValueError("The model has no grid; draw the section along a `line` or `through` named elements.")
    raise ValueError(f"No grid line {tag!r}. The grid lines: {', '.join(t for t in tags if t)}.")


class View:
    """Where a view looks from: paper's right (u) and up (v) axes and its depth (d, away from the viewer), in metres."""

    def __init__(self, right: np.ndarray, up: np.ndarray, toward: np.ndarray, origin: np.ndarray):
        self.right, self.up, self.toward, self.origin = right, up, toward, origin

    def uvd(self, pts: np.ndarray) -> np.ndarray:
        p = pts - self.origin
        return np.stack([p @ self.right, p @ self.up, p @ self.toward], axis=-1)


def plan_view(z: float) -> View:
    """Seen from above: x east to the right, y north up, depth downwards from the cut."""
    return View(np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]), np.array([0, 0, z]))


def upright_view(looking: np.ndarray, origin: np.ndarray) -> View:
    """Looking horizontally along `looking`: its right to the right, z up."""
    right = np.cross(looking, np.array([0, 0, 1.0]))
    return View(right / np.linalg.norm(right), np.array([0, 0, 1.0]), looking / np.linalg.norm(looking), origin)


# ---------------------------------------------------------------- svg


def _path(geom, k: float, ox: float, oy: float) -> str:
    """SVG path data for a shapely geometry; paper x = ox + u*k, paper y = oy - v*k."""
    parts: list[str] = []

    def ring(coords, close: bool) -> None:
        c = np.asarray(coords)
        if len(c) < 2:
            return
        xs = ox + c[:, 0] * k
        ys = oy - c[:, 1] * k
        pts = " ".join(f"{x:.2f} {y:.2f}" for x, y in zip(xs, ys))
        parts.append(f"M{pts}{'Z' if close else ''}")

    def walk(g) -> None:
        t = g.geom_type
        if t == "Polygon":
            ring(g.exterior.coords, True)
            for i in g.interiors:
                ring(i.coords, True)
        elif t in ("LineString", "LinearRing"):
            ring(g.coords, g.is_closed)
        elif hasattr(g, "geoms"):
            for x in g.geoms:
                walk(x)

    walk(geom)
    return "".join(parts)


def _attrs(e: ifcopenshell.entity_instance) -> str:
    return f'data-id="{e.id()}" data-ifc="{e.is_a()}"' + (f" data-guid={quoteattr(e.GlobalId)}" if getattr(e, "GlobalId", None) else "")


def _text(x: float, y: float, s: str, size: float, anchor: str = "start", weight: str = "normal", halo: bool = False) -> str:
    body = f'x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" text-anchor="{anchor}" font-weight="{weight}"'
    # The halo is its own text under the words: cairosvg does not keep paint-order.
    under = f'<text {body} style="fill:#fff;stroke:#fff;stroke-width:0.7;stroke-linejoin:round">{escape(s)}</text>' if halo else ""
    return f"{under}<text {body}>{escape(s)}</text>"


def _nice(step_target: float, steps: tuple[float, ...]) -> float:
    for s in steps:
        if s >= step_target:
            return s
    return steps[-1]


def _scale_bars(right: float, y: float, ratio: int) -> list[str]:
    """Two scale bars, metres and feet, of four steps of 12 to 30 mm of paper, ending at `right`."""
    out = []
    k = 1000.0 / ratio  # paper mm per metre
    for row, (unit, per_m, steps) in enumerate((("m", 1.0, (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200)), ("ft", 1 / 0.3048, (0.5, 1, 2, 4, 5, 10, 20, 25, 50, 100, 200, 500)))):
        step = _nice(12.0 / k * per_m, steps)  # at least 12 mm a step
        mm = step / per_m * k
        x = right - 6 - 4 * mm
        yy = y + row * 7.0
        for i in range(4):
            fill = "#000" if i % 2 == 0 else "#fff"
            out.append(f'<rect x="{x + i * mm:.2f}" y="{yy:.2f}" width="{mm:.2f}" height="1.6" fill="{fill}" stroke="#000" stroke-width="0.15"/>')
        for i in range(5):
            out.append(_text(x + i * mm, yy + 4.4, f"{step * i:g}", 2.0, "middle"))
        out.append(_text(x + 4 * mm + 2.5, yy + 1.8, unit, 2.2))
    return out


def _north_arrow(x: float, y: float, north: tuple[float, float] | None) -> list[str]:
    nx, ny = north or (0.0, 1.0)
    ang = math.degrees(math.atan2(nx, ny))  # clockwise from paper up
    return [
        f'<g transform="translate({x:.2f} {y:.2f}) rotate({ang:.2f})">'
        '<circle r="5" fill="none" stroke="#000" stroke-width="0.25"/>'
        '<path d="M0 -6 L2.2 3 L0 1.6 L-2.2 3 Z" fill="#000"/>'
        "</g>",
        _text(x, y + 9.5, "N" if north is None or north == (0.0, 1.0) else "N (true)", 2.6, "middle", "bold"),
    ]


# ---------------------------------------------------------------- room labels

#: One room holds another when at least this share of the other's drawn area
#: lies inside it, and the other is the smaller. Not exact containment: rooms
#: are drawn snapped to the paper's grid, and a zone traced inside a room
#: often runs a hair past its edge. Rooms that merely overlap are not nested.
HOLDS_SHARE = 0.9

#: How wide a character of a room's label is, as a share of its font size, on
#: the generous side (DejaVu Sans averages about 0.6 em, bold about 0.65).
_EM_BOLD, _EM = 0.7, 0.62
#: Paper kept clear around a label, in millimetres.
_LABEL_PAD = 0.6


#: A stored room area and the area measured from its outline that differ by more
#: than this share of the stored one are named in the reply, with both numbers.
AREA_DIFFERS = 0.05


def stored_room_area(sp: ifcopenshell.entity_instance) -> tuple[float, str] | None:
    """The area the model STORES for a room, in the model's area unit, and where
    it is stored: Qto_SpaceBaseQuantities.NetFloorArea first (IFC's own base
    quantity), then an "Area" in any other property or quantity set (a Revit
    export keeps it in its "Dimensions" set, which is looked at first). None
    when the model stores no area for it."""
    psets = ifcopenshell.util.element.get_psets(sp)

    def number(v: Any) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None

    v = number(psets.get("Qto_SpaceBaseQuantities", {}).get("NetFloorArea"))
    if v is not None:
        return v, "Qto_SpaceBaseQuantities.NetFloorArea"
    for name in sorted(psets, key=lambda n: (n != "Dimensions", n)):
        v = number(psets[name].get("Area"))
        if v is not None:
            return v, f"{name}.Area"
    return None


def _room_name(sp: ifcopenshell.entity_instance) -> str:
    return " ".join(t for t in (sp.Name, getattr(sp, "LongName", None)) if t)


def rooms_inside(room_shapes: list[tuple[Any, Any, float]]) -> list[list[int]]:
    """For each room drawn, the indices of the rooms it holds (HOLDS_SHARE of their drawn area inside it)."""
    polys = [p for _, p, _ in room_shapes]
    areas = [p.area for p in polys]
    held: list[list[int]] = [[] for _ in polys]
    for i, outer in enumerate(polys):
        for j, inner in enumerate(polys):
            if i == j or areas[j] <= 0 or areas[j] >= areas[i]:
                continue
            if not outer.intersects(inner):
                continue
            if outer.intersection(inner).area >= HOLDS_SHARE * areas[j]:
                held[i].append(j)
    return held


def label_point(free, name: str, area_text: str, k: float):
    """Where a label fits wholly inside `free` (a room's part clear of the rooms it holds), or None.

    The label is the name in bold at 2.4 mm and, in a plan, its area at 2.0 mm
    3 mm below; its anchor is the middle of the name's baseline, as drawn. Of
    the places it fits, the one farthest from the edges of `free` is taken,
    and of those the nearest the middle of its part.
    `k` is paper millimetres per metre."""
    import shapely
    from shapely.ops import polylabel

    if free.is_empty or free.area <= 0:
        return None
    w = max(len(name) * 2.4 * _EM_BOLD, len(area_text) * 2.0 * _EM) + 2 * _LABEL_PAD
    above = 2.4 * 0.8 + _LABEL_PAD
    below = (3.0 + 2.0 * 0.25 if area_text else 2.4 * 0.25) + _LABEL_PAD
    half_w, half_h, rise = w / 2 / k, (above + below) / 2 / k, (above - below) / 2 / k  # metres; rise: the box's middle above the anchor
    pieces = [g for g in getattr(free, "geoms", [free]) if g.geom_type == "Polygon" and g.area > 0]
    step = 0.5 / k  # clearances within half a millimetre of paper are as good as each other
    best = None
    for piece in pieces:
        x0, y0, x1, y1 = piece.bounds
        if x1 - x0 < 2 * half_w or y1 - y0 < 2 * half_h:
            continue
        # Its middle, as any other room's label takes it; polylabel when the centroid falls outside.
        middle = piece.centroid if piece.contains(piece.centroid) else polylabel(piece, tolerance=min(half_w, half_h) / 4)
        n = 24
        centres = [middle] + [shapely.Point(x0 + (x1 - x0) * (a + 0.5) / n, y0 + (y1 - y0) * (b + 0.5) / n) for a in range(n) for b in range(n)]
        for c in centres:
            box = shapely.box(c.x - half_w, c.y - half_h, c.x + half_w, c.y + half_h)
            if not piece.covers(box):
                continue
            # Farthest from the edges first; of those, nearest the middle of its part.
            score = (math.floor(piece.boundary.distance(box) / step), -c.distance(middle))
            if best is None or score > best[0]:
                best = (score, shapely.Point(c.x, c.y - rise))
    return None if best is None else best[1]


# ---------------------------------------------------------------- drawing


STYLE = (
    "<style>"
    "text{font-family:'DejaVu Sans',Arial,Helvetica,sans-serif;fill:#000}"
    ".seen{fill:#fff;stroke:#000;stroke-width:0.13;stroke-linejoin:round}"
    ".cut{fill:#444;stroke:#000;stroke-width:0.35;stroke-linejoin:round;fill-rule:evenodd}"
    ".cutline{fill:none;stroke:#000;stroke-width:0.35;stroke-linejoin:round}"
    ".room{fill:none;stroke:#3a6ea5;stroke-width:0.15;stroke-dasharray:1.2 0.8}"
    ".level{fill:none;stroke:#3a6ea5;stroke-width:0.15;stroke-dasharray:4 1 0.6 1}"
    ".hl.seen{fill:#ffd9b3;stroke:#d9480f;stroke-width:0.3}"
    ".hl.cut{fill:#d9480f;stroke:#7a2900}"
    ".hl.cutline{stroke:#d9480f;stroke-width:0.5}"
    ".hl.room{stroke:#d9480f;stroke-width:0.4;fill:#d9480f;fill-opacity:0.12}"
    "</style>"
)


def draw(
    model: ifcopenshell.file,
    output_path: str,
    view: str = "plan",
    model_path: str | None = None,
    storey: str = "",
    cut_height: float | None = None,
    line: str = "",
    through: str = "",
    direction: str = "",
    gridline: str = "",
    looking: str = "",
    side: str = "",
    around: str = "",
    margin: float | None = None,
    depth: float | None = None,
    highlight: str = "",
    scale: str = "",
    paper: str = "",
    title: str = "",
    png_px: int = 2000,
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Draw a plan, section or elevation to scale, as `output_path`.svg and `output_path`.png. See server.py's ifc_draw."""
    import shapely

    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    deadline = started + budget
    u = geometry.Units(model)
    m_per_unit = u.scale
    to_m = lambda v: float(v) * m_per_unit  # noqa: E731
    view = (view or "plan").strip().lower()
    if view not in ("plan", "section", "elevation"):
        raise ValueError(f'{view!r} is not a view: "plan", "section" or "elevation".')
    stem = re.sub(r"\.(svg|png)$", "", output_path.strip(), flags=re.I)
    if not stem:
        raise ValueError("`output_path` says where the drawing is written: <output_path>.svg and <output_path>.png.")
    width_mm, height_mm, paper_name = paper_of(paper)
    margin_m = to_m(margin) if margin is not None else 1.0
    highlighted = elements_named(model, highlight)
    hl_ids = {e.id() for e in highlighted}
    notes: list[str] = []

    # 1. WHAT IS DRAWN, and from where.
    levels: list[tuple[str, float]] = []
    spaces: list[ifcopenshell.entity_instance] = []
    if view == "plan":
        if not storey.strip():
            raise ValueError("A plan is of one storey: name it with `storey` (ifc_tree lists them, e.g. \"Level 1\").")
        st = reading.find_storey(model, storey)
        z0 = storey_z_m(model, st)
        cut = to_m(cut_height) if cut_height is not None else 1.2
        elements, spaces = storey_elements(st)
        v = plan_view(z0 + cut)
        what = f"Plan of {st.Name}, cut {u.length_of(cut)['model']:,} {u.length} ({u.length_of(cut)['feet_inches']}) above its level"
        if getattr(st, "Elevation", None) is None and reading._storey_z(st) is None:
            notes.append("The storey has no elevation or placement; its level is taken as 0.")
    else:
        sts = storeys_named(model, storey)
        elements = sorted({e.id(): e for s in sts for e in storey_elements(s)[0]}.values(), key=lambda e: e.id())
        spaces = sorted({e.id(): e for s in sts for e in storey_elements(s)[1]}.values(), key=lambda e: e.id())
        levels = [(s.Name or f"#{s.id()}", storey_z_m(model, s)) for s in sts]
        if view == "section":
            named = [bool(line.strip()), bool(through.strip()), bool(gridline.strip())]
            if sum(named) != 1:
                raise ValueError("A section is cut along ONE of: `line` (x1, y1, x2, y2 in the model's unit), `gridline` (a grid line's tag), or `through` (elements it cuts through the middle of, with `direction`).")
            if line.strip():
                nums = [float(x) for x in re.split(r"[\s,;]+", line.strip()) if x]
                if len(nums) != 4:
                    raise ValueError("`line` is four numbers: x1, y1, x2, y2, in the model's length unit (as ifc_extent gives them).")
                p0, p1 = np.array([to_m(nums[0]), to_m(nums[1]), 0.0]), np.array([to_m(nums[2]), to_m(nums[3]), 0.0])
                along_text = f"along the line ({nums[0]:g}, {nums[1]:g}) to ({nums[2]:g}, {nums[3]:g}) {u.length}"
            elif gridline.strip():
                p0, p1 = grid_line(model, gridline)
                p0[2] = p1[2] = 0.0
                along_text = f"along grid line {gridline.strip()}"
            else:
                cut_through = elements_named(model, through)
                got, stopped = geometry.meshes(model, cut_through, deadline)
                if not got:
                    raise ValueError(f"Nothing named by `through` ({through!r}) has a shape to cut through.")
                lo = np.min([m.verts.min(0) for m in got.values()], axis=0)
                hi = np.max([m.verts.max(0) for m in got.values()], axis=0)
                c = (lo + hi) / 2
                d = (direction or "north-south").strip().lower().replace(" ", "-")
                if d in ("north-south", "south-north", "ns", "n-s", "y"):
                    p0, p1 = np.array([c[0], lo[1], 0.0]), np.array([c[0], hi[1], 0.0])
                elif d in ("east-west", "west-east", "ew", "e-w", "x"):
                    p0, p1 = np.array([lo[0], c[1], 0.0]), np.array([hi[0], c[1], 0.0])
                else:
                    raise ValueError(f'`direction` is the way the cut runs: "north-south" or "east-west", not {direction!r}.')
                names = ", ".join(sorted({(e.Name or f"#{e.id()}") for e in cut_through}))[:120]
                along_text = f"{d} through the middle of {names}"
            run = p1 - p0
            if np.linalg.norm(run[:2]) < 1e-6:
                raise ValueError("The section line's two ends are the same point.")
            run = run / np.linalg.norm(run)
            left = np.array([-run[1], run[0], 0.0])
            toward = left
            if looking.strip():
                want = _compass(looking)
                if abs(float(want @ run)) > 0.71:
                    raise ValueError(f"A section along this line looks across it; {looking!r} looks along it. Look {'north or south' if abs(run[0]) > abs(run[1]) else 'east or west'}.")
                toward = left if float(want @ left) > 0 else -left
            v = upright_view(toward, p0)
            look_word = min(("north", "south", "east", "west"), key=lambda w: float(np.linalg.norm(_compass(w) - toward)))
            what = f"Section {along_text}, looking {look_word}"
        else:
            if not side.strip():
                raise ValueError('An elevation is of one side: `side` "north", "south", "east" or "west" (the side seen).')
            toward = -_compass(side)
            v = upright_view(toward, np.zeros(3))
            what = f"{side.strip().capitalize()} elevation"
        if storey.strip():
            what += f", {', '.join(s.Name or '' for s in sts)}"
    if title.strip():
        what = title.strip()

    # 2. THE MESHES (kept between calls: a second drawing does not make them again).
    todo = elements + [s for s in spaces] + [e for e in highlighted if e.id() not in {x.id() for x in elements}]
    got, stopped = geometry.meshes(model, todo, deadline)
    if stopped:
        return {
            "view": view,
            "drawn": False,
            "partial": True,
            "stopped": (
                f"Time ran out ({budget:.0f} s) after making {len(got)} of {len(todo)} shapes, before drawing. They are kept: call again with the same "
                "arguments and it goes on from there. Or draw less: `storey` (one or a few), or `around` (a region)."
            ),
            "seconds": round(time.monotonic() - started, 1),
            "units": u.say,
        }
    extra_hl = [e for e in highlighted if e.id() not in {x.id() for x in elements} and not e.is_a("IfcSpace") and e.id() in got]
    drawn_elements = [e for e in elements if e.id() in got] + extra_hl
    no_shape = sum(1 for e in elements if e.id() not in got)

    # 3. THE REGION (optional) and the depth.
    region = None
    if around.strip():
        near = elements_named(model, around)
        near_got, _ = geometry.meshes(model, near, deadline)
        if not near_got:
            raise ValueError(f"Nothing named by `around` ({around!r}) has a shape.")
        pts = np.concatenate([m.verts for m in near_got.values()])
        uvd = v.uvd(pts)
        if view == "plan":
            region = shapely.box(uvd[:, 0].min() - margin_m, uvd[:, 1].min() - margin_m, uvd[:, 0].max() + margin_m, uvd[:, 1].max() + margin_m)
        else:
            region = shapely.box(uvd[:, 0].min() - margin_m, -1e6, uvd[:, 0].max() + margin_m, 1e6)
    max_depth = to_m(depth) if depth is not None and depth > 0 else None

    # 4. FRAME AND SCALE, from the parts that will be seen.
    boxes = []
    for e in drawn_elements:
        q = v.uvd(got[e.id()].verts)
        keep = q[:, 2] >= -1e-9 if view != "elevation" else np.ones(len(q), bool)
        if view == "plan":
            keep = np.ones(len(q), bool)  # the plan's frame is everything of the storey
        if max_depth is not None:
            keep &= q[:, 2] <= max_depth
        if keep.any():
            boxes.append((q[keep, 0].min(), q[keep, 1].min(), q[keep, 0].max(), q[keep, 1].max()))
    if not boxes:
        raise ValueError("Nothing in this view has a shape to draw: check the storey, the line and `looking`.")
    b = np.array(boxes)
    frame = [b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()]
    if region is not None:
        rb = region.bounds
        frame = [max(frame[0], rb[0]), max(frame[1], rb[1]), min(frame[2], rb[2]), min(frame[3], rb[3])]
        if frame[0] >= frame[2] or frame[1] >= frame[3]:
            raise ValueError("The region named by `around` holds nothing of this view.")
    pad = 0.3
    frame = [frame[0] - pad, frame[1] - pad, frame[2] + pad, frame[3] + pad]
    fw, fh = frame[2] - frame[0], frame[3] - frame[1]
    label_room = 34.0 if view != "plan" and levels else 0.0  # the levels' names, right of the drawing
    best = None
    for (pw, ph) in ((width_mm, height_mm), (height_mm, width_mm)):
        aw, ah = pw - 2 * MARGIN_MM - label_room, ph - 2 * MARGIN_MM - TITLE_MM
        fits = [r for r in CANDIDATES if fw * 1000 / r <= aw and fh * 1000 / r <= ah]
        if fits and (best is None or fits[0] < best[0]):
            best = (fits[0], pw, ph, aw, ah)
    if scale.strip():
        ratio = parse_scale(scale)
        chosen = None
        for (pw, ph) in ((width_mm, height_mm), (height_mm, width_mm)):
            aw, ah = pw - 2 * MARGIN_MM - label_room, ph - 2 * MARGIN_MM - TITLE_MM
            if fw * 1000 / ratio <= aw and fh * 1000 / ratio <= ah:
                chosen = (ratio, pw, ph, aw, ah)
                break
        if chosen is None:
            fit = f"; at most {scale_say(best[0])} fits it" if best else ""
            raise ValueError(
                f"At {scale_say(ratio)} this view ({u.length_of(fw)['model']:,} x {u.length_of(fh)['model']:,} {u.length}) does not fit on {paper_name}{fit}. "
                "Give a smaller scale, a larger `paper`, or draw less with `around`."
            )
        best = chosen
    if best is None:
        raise ValueError(f"This view ({u.length_of(fw)['model']:,} x {u.length_of(fh)['model']:,} {u.length}) does not fit on {paper_name} at 1:{CANDIDATES[-1]}; draw less with `around` or `storey`.")
    ratio, pw, ph, aw, ah = best
    k = 1000.0 / ratio  # paper mm per metre
    grid = 0.0001 * ratio  # a tenth of a millimetre of paper, in metres
    ox = MARGIN_MM + (aw - fw * k) / 2 - frame[0] * k
    oy = MARGIN_MM + (ah - fh * k) / 2 + frame[3] * k

    # 5. PROJECT AND CUT, element by element.
    seen: list[tuple[float, ifcopenshell.entity_instance, Any]] = []
    cuts: list[tuple[ifcopenshell.entity_instance, list[Any]]] = []
    hidden_above = 0
    for e in drawn_elements:
        if time.monotonic() > deadline:
            return {
                "view": view,
                "drawn": False,
                "partial": True,
                "stopped": f"Time ran out ({budget:.0f} s) while drawing {len(drawn_elements)} elements. The shapes are kept, so a second call is faster; or draw less with `storey` or `around`.",
                "seconds": round(time.monotonic() - started, 1),
                "units": u.say,
            }
        tri = v.uvd(got[e.id()].triangles)
        s = -tri[:, :, 2]  # keep depth >= 0: beyond the cut, or below it
        if view == "elevation":
            beyond = tri
        else:
            beyond = clip_keep(tri, s)
        if max_depth is not None and len(beyond):
            beyond = clip_keep(beyond, beyond[:, :, 2] - max_depth)
        whole = len(beyond) == len(tri) and (view == "elevation" or bool((s <= 0).all())) and (max_depth is None or bool((tri[:, :, 2] <= max_depth).all()))
        if len(beyond) == 0:
            hidden_above += 1
            continue
        if view != "elevation":
            segs = cut_segments(tri, s)
            if len(segs):
                lines = cut_lines(segs[:, :, :2], grid)
                if region is not None:
                    lines = [shapely.intersection(g, region) for g in lines]
                lines = [g for g in lines if not g.is_empty]
                if lines:
                    cuts.append((e, lines))
        # A closed mesh's triangles facing one way cover its whole outline
        # (whichever way: those facing away cover it too), so half suffice.
        closed = whole and geometry.closure_of(model, got[e.id()])["status"] != "open"
        poly = silhouette(beyond[:, :, :2], grid, front_only=closed)
        if region is not None:
            poly = shapely.intersection(poly, region)
        if poly.is_empty:
            continue
        seen.append((float(beyond[:, :, 2].min()), e, poly))
    seen.sort(key=lambda r: -r[0])  # farthest first, so nearer ones cover them

    room_shapes: list[tuple[ifcopenshell.entity_instance, Any, float]] = []
    for sp in spaces:
        mesh = got.get(sp.id())
        if mesh is None:
            continue
        tri = v.uvd(mesh.triangles)
        if view == "plan":
            poly = silhouette(tri[:, :, :2], grid)
        else:
            segs = cut_segments(tri, -tri[:, :, 2])
            if not len(segs):
                continue
            lines = cut_lines(segs[:, :, :2], grid)
            polys = [shapely.Polygon(g.coords) for g in lines if g.is_closed and len(g.coords) >= 4]
            poly = shapely.union_all(polys) if polys else shapely.Polygon()
        if region is not None:
            poly = shapely.intersection(poly, region)
        if not poly.is_empty:
            room_shapes.append((sp, poly, geometry.footprint_of(mesh).area))

    # 6. THE SHEET.
    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{pw:g}mm" height="{ph:g}mm" viewBox="0 0 {pw:g} {ph:g}">',
        STYLE,
        f'<rect x="0" y="0" width="{pw:g}" height="{ph:g}" fill="#fff"/>',
        f'<rect x="{MARGIN_MM / 2:g}" y="{MARGIN_MM / 2:g}" width="{pw - MARGIN_MM:g}" height="{ph - MARGIN_MM:g}" fill="none" stroke="#000" stroke-width="0.35"/>',
        '<g id="seen">',
    ]
    for _, e, poly in seen:
        cls = "hl seen" if e.id() in hl_ids else "seen"
        svg.append(f'<path class="{cls}" {_attrs(e)} d="{_path(poly, k, ox, oy)}"/>')
    svg.append('</g><g id="rooms">')
    for sp, poly, _ in room_shapes:
        cls = "hl room" if sp.id() in hl_ids else "room"
        svg.append(f'<path class="{cls}" {_attrs(sp)} d="{_path(poly, k, ox, oy)}"/>')
    svg.append('</g><g id="cut">')
    for e, lines in cuts:
        closed = [g for g in lines if g.geom_type == "LineString" and g.is_closed and len(g.coords) >= 4]
        open_ = [g for g in lines if not (g.geom_type == "LineString" and g.is_closed and len(g.coords) >= 4)]
        hl = "hl " if e.id() in hl_ids else ""
        if closed:
            svg.append(f'<path class="{hl}cut" {_attrs(e)} d="{"".join(_path(g, k, ox, oy) for g in closed)}"/>')
        if open_:
            svg.append(f'<path class="{hl}cutline" {_attrs(e)} d="{"".join(_path(g, k, ox, oy) for g in open_)}"/>')
    svg.append('</g><g id="labels">')
    unlabelled: list[dict[str, Any]] = []
    room_areas: list[dict[str, Any]] = []
    for (sp, poly, area), held in zip(room_shapes, rooms_inside(room_shapes)):
        name = _room_name(sp)
        # The label shows the area the model stores for the room, when it
        # stores one (the authoring tool's own number), and the area measured
        # from the room's outline only when it stores none.
        measured = u.area_of(area)
        stored = stored_room_area(sp) if view == "plan" else None
        a = u.area_of(stored[0] * u.area_si) if stored else measured
        if view == "plan":
            entry: dict[str, Any] = {"id": sp.id(), "name": name, "global_id": getattr(sp, "GlobalId", None), "source": "stored" if stored else "measured", "area": a}
            if stored:
                entry["stored_in"] = stored[1]
                entry["measured"] = measured
                if abs(measured["square_metres"] - a["square_metres"]) > AREA_DIFFERS * a["square_metres"]:
                    entry["differs"] = True
                    notes.append(
                        f"{name or 'A room'} (#{sp.id()}): the model stores {a['model']:,} {u.area} ({a['square_metres']:,.1f} m², {a['square_feet']:,.0f} ft²) in {stored[1]}, "
                        f"and its outline measures {measured['model']:,} {u.area} ({measured['square_metres']:,.1f} m², {measured['square_feet']:,.0f} ft²), "
                        f"{abs(measured['square_metres'] - a['square_metres']) / a['square_metres']:.0%} apart. The label shows the stored area."
                    )
            room_areas.append(entry)
        area_text = f"{a['square_metres']:,.1f} m² ({a['square_feet']:,.0f} ft²)" if view == "plan" else ""
        if held:
            # A room that holds others is named in its part clear of them, or not on the sheet at all.
            pt = label_point(poly.difference(shapely.union_all([room_shapes[j][1] for j in held])), name, area_text, k)
            if pt is None:
                inner = [_room_name(room_shapes[j][0]) or f"#{room_shapes[j][0].id()}" for j in held]
                unlabelled.append({"id": sp.id(), "name": name, "global_id": getattr(sp, "GlobalId", None), "contains": inner})
                if room_areas and room_areas[-1]["id"] == sp.id():
                    room_areas[-1]["labelled"] = False
                continue
        else:
            pt = poly.centroid if poly.contains(poly.centroid) else poly.representative_point()
        x, y = ox + pt.x * k, oy - pt.y * k
        svg.append(_text(x, y, name, 2.4, "middle", "bold", halo=True))
        if area_text:
            svg.append(_text(x, y + 3.0, area_text, 2.0, "middle", halo=True))
    if levels:
        x0, x1 = MARGIN_MM, pw - MARGIN_MM - label_room + 2
        for name, z in levels:
            y = oy - (z - v.origin[2]) * k
            if MARGIN_MM <= y <= ph - MARGIN_MM - TITLE_MM:
                svg.append(f'<path class="level" d="M{x0:.2f} {y:.2f}H{x1:.2f}"/>')
                say = u.length_of(z)
                svg.append(_text(x1 + 1, y - 0.6, name, 2.2, weight="bold"))
                svg.append(_text(x1 + 1, y + 2.4, f"{say['model']:,} {u.length} ({say['feet_inches']})", 1.9))
    svg.append("</g>")

    # The title block: what, the scale, the unit, the sheet and the source.
    ty = ph - MARGIN_MM - TITLE_MM + 4
    svg.append(f'<path d="M{MARGIN_MM / 2:g} {ty - 4:.2f}H{pw - MARGIN_MM / 2:g}" stroke="#000" stroke-width="0.25"/>')
    sha = geometry.file_sha256(model_path)
    source = f"Drawn from {os.path.basename(model_path) if model_path else 'a model made in this session'}" + (f" (sha256 {sha[:12]}…)" if sha else "")
    if edits:
        source += f", as edited in the session ({edits} change(s), not saved to that file)"
    svg.append(_text(MARGIN_MM, ty + 2, what, 3.6, weight="bold"))
    svg.append(_text(MARGIN_MM, ty + 7.5, f"Scale {scale_say(ratio)} on {paper_name}. The model's lengths are in {u.length_name}.", 2.6))
    svg.append(_text(MARGIN_MM, ty + 12, f"{source}; flo2-ifc {geometry._version()}. Dark: cut. Outlined: seen beyond{' or below' if view == 'plan' else ''}." + (" Orange: highlighted." if hl_ids else ""), 2.1))
    svg.extend(_scale_bars(pw - MARGIN_MM, ty - 1, ratio))
    if view == "plan":
        svg.extend(_north_arrow(pw - MARGIN_MM - 140, ty + 4, _true_north(model)))
    svg.append("</svg>")
    text = "\n".join(svg)

    # 7. WRITE IT, and its picture.
    import cairosvg

    os.makedirs(os.path.dirname(os.path.abspath(stem)) or ".", exist_ok=True)
    with open(stem + ".svg", "w", encoding="utf-8") as f:
        f.write(text)
    long_side = max(400, min(int(png_px), 6000))
    px_w, px_h = (long_side, round(long_side * ph / pw)) if pw >= ph else (round(long_side * pw / ph), long_side)
    png = cairosvg.svg2png(bytestring=text.encode("utf-8"), output_width=px_w, output_height=px_h, background_color="white")
    with open(stem + ".png", "wb") as f:
        f.write(png)

    out: dict[str, Any] = {
        "view": view,
        "what": what,
        "drawn": {"svg": stem + ".svg", "png": stem + ".png", "svg_bytes": len(text.encode("utf-8")), "png_bytes": len(png), "png_px": [px_w, px_h]},
        "sheet": {"paper": paper_name, "orientation": "landscape" if pw >= ph else "portrait", "size_mm": [pw, ph], "scale": scale_say(ratio), "ratio": ratio},
        "frame": {
            "min": [round(frame[0] / m_per_unit, u.decimals), round(frame[1] / m_per_unit, u.decimals)],
            "max": [round(frame[2] / m_per_unit, u.decimals), round(frame[3] / m_per_unit, u.decimals)],
            "unit": u.length,
            "axes": "x east, y north (plan)" if view == "plan" else "across the sheet to the right, then height (z)",
        },
        "elements": {
            "drawn": len(seen),
            "cut": len(cuts),
            "rooms": len(room_shapes),
            "highlighted": len([1 for _, e, _ in seen if e.id() in hl_ids]) + len([1 for sp, _, _ in room_shapes if sp.id() in hl_ids]),
            "not_drawn": {"no_shape": no_shape, "above_the_cut_or_behind_the_viewer": hidden_above},
        },
    }
    if highlighted:
        out["highlight"] = [reading._brief(e) | {"global_id": getattr(e, "GlobalId", None)} for e in highlighted[:50]]
        if len(highlighted) > 50:
            out["highlight_withheld"] = len(highlighted) - 50
    if view != "plan":
        out["looking"] = {"toward": [round(float(x), 4) for x in v.toward[:2]], "right": [round(float(x), 4) for x in v.right[:2]]}
    if room_areas:
        out["room_areas"] = room_areas
    if unlabelled:
        out["unlabelled_rooms"] = unlabelled
        for r in unlabelled:
            inner = r["contains"]
            notes.append(
                f"Not named on the sheet: {r['name'] or 'a room'} (#{r['id']}). It holds {len(inner)} other room(s) ({', '.join(inner[:6])}{', ...' if len(inner) > 6 else ''}), "
                "and the part of it clear of them has no space for its name."
            )
    tn = _true_north(model)
    if tn and abs(tn[0]) > 1e-6:
        notes.append(f"The model's true north is ({tn[0]:g}, {tn[1]:g}), not its +y: north, south, east and west here are the model's own axes.")
    if notes:
        out["notes"] = notes
    out["source"] = {"model_file": os.path.basename(model_path) if model_path else None, "sha256": sha}
    out["seconds"] = round(time.monotonic() - started, 1)
    out["units"] = u.say
    return out

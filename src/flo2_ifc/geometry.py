"""Measuring a building model from its geometry: footprints, volumes, distances.

ifc_extent (reading.py) measures how far elements reach. The numbers a design
rests on need more: how much ground a set of elements covers, how much solid
they hold, and how far apart two of them are. Each of these is read from the
elements' triangle meshes, as IfcOpenShell's geometry iterator makes them, in
world coordinates and METRES, and given back in the model's own unit, in
metres, and in feet and inches.

- A FOOTPRINT is the union of an element's triangles projected onto plan, so a
  set's footprint counts what is stacked (a slab on a footing, a wall over a
  wall) once. The reply gives the sum of each element's own footprint too, and
  the difference is what was stacked.
- A VOLUME is a solid's only when its mesh is CLOSED. The volume of an open
  mesh depends on where it is taken from (2026-10-23: a model's roofs were
  seen to store volumes larger than their own bounding boxes, the signed
  volume of their open meshes in world coordinates). So an open mesh is named and its
  volume refused, never summed.
- A DISTANCE is the shortest gap between two elements' surfaces, with the two
  closest points, and the gap between their boxes along each axis.

Every reply carries ``cite``: what flo2-calc needs to keep a number with its
source (the model file's name and sha256, the elements' GlobalIds, the unit and
the method). These functions take an open ``ifcopenshell.file`` and never write
it.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
import weakref
from dataclasses import dataclass
from typing import Any

import ifcopenshell
import ifcopenshell.util.selector
import numpy as np

from flo2_ifc import reading

# ---------------------------------------------------------------- the meshes


@dataclass(frozen=True)
class Mesh:
    """One element's shape: vertices in world coordinates, METRES, and triangles."""

    id: int
    verts: np.ndarray  # (n, 3) float64
    faces: np.ndarray  # (m, 3) int32
    #: Whether the element's openings were cut from it (meshes(), "Openings").
    openings_cut: bool = True

    @property
    def triangles(self) -> np.ndarray:
        """(m, 3, 3): each triangle's three corners."""
        return self.verts[self.faces]

    @property
    def nbytes(self) -> int:
        return int(self.verts.nbytes + self.faces.nbytes)


#: Megabytes of meshes a session keeps between calls, so a second drawing or
#: measure of the same elements does not mesh them again. Every mesh of a
#: large building model is some tens of MB (measured 2026-10-24).
CACHE_MB = float(os.environ.get("FLO2_IFC_MESH_CACHE_MB", "256"))

#: Bumped whenever the session's model may have changed (server.py: an edit, a
#: shape, a new or reloaded model). A mesh is kept only for the generation it
#: was made in, so a drawing after an edit draws the edited model.
_generation = 0
#: Kept meshes by (id(model), openings_cut, element id): a mesh is kept per setting, so a mesh made whole is never
#: handed out where one with its openings cut was asked for.
_cache: dict[tuple[int, bool, int], Mesh] = {}
_cache_bytes = 0


def changed() -> None:
    """The session's model may have changed: forget every kept mesh."""
    global _generation, _cache_bytes
    _generation += 1
    _cache.clear()
    _closures.clear()
    _cache_bytes = 0


def generation() -> int:
    return _generation


#: A finalizer per model with kept meshes. The meshes are kept by id(model),
#: and Python gives a freed object's id to the next one made: without this, a
#: model made after another was freed could be measured or drawn with the
#: first one's meshes (seen in the tests 2026-10-24). The server also forgets
#: every mesh when its model changes (changed()); this does not rely on that.
_owners: dict[int, weakref.finalize] = {}


def _forget_model(key: int) -> None:
    """A model was freed: drop its meshes and closures before its id is reused."""
    global _cache_bytes
    _owners.pop(key, None)
    for k in [k for k in list(_cache) if k[0] == key]:
        mesh = _cache.pop(k, None)
        if mesh is not None:
            _cache_bytes -= mesh.nbytes
    for k in [k for k in list(_closures) if k[0] == key]:
        _closures.pop(k, None)
    _cache_bytes = max(_cache_bytes, 0)


def _keep(model: ifcopenshell.file, mesh: Mesh) -> None:
    global _cache_bytes
    if _cache_bytes + mesh.nbytes > CACHE_MB * 1024 * 1024:
        return
    key = id(model)
    if key not in _owners:
        _owners[key] = weakref.finalize(model, _forget_model, key)
    _cache[(key, mesh.openings_cut, mesh.id)] = mesh
    _cache_bytes += mesh.nbytes


def mesh_settings(cut_openings: bool = True):
    """IfcOpenShell's geometry settings for meshes: world coordinates, and openings CUT unless told otherwise.

    Since 0.8.0 (dec:building-steps-take-a-frame-and-measures-cut-openings, from
    fact:measures-and-drawings-skip-opening-subtraction-so-a-cut-wall-reads-whole-2026-10-25) the meshes have their
    own settings. Until then they shared ifc_extent's (reading._geometry_settings), which skip opening subtraction
    because an opening cannot widen a box; on the premise that a Revit export's meshes come already cut. That holds
    for a Revit export and not for an opening made in the model (feature.add_feature, as the skill's recipe and
    ifc_edit make one): such a wall was measured whole (3.0 m3 for 2.622) and drawn solid, with its door hidden. A
    volume, a footprint, a distance and a drawing need the shape as built, so they take the cut one."""
    s = reading._geometry_settings()
    s.set("disable-opening-subtractions", not cut_openings)
    return s


def meshes(model: ifcopenshell.file, elements: list[ifcopenshell.entity_instance], deadline: float, cut_openings: bool = True) -> tuple[dict[int, Mesh], bool]:
    """Each element's mesh, by id, made once a session per setting, and whether time ran out.

    Elements with no 3D shape are left out, and so are openings themselves. OPENINGS ARE CUT (``cut_openings``,
    mesh_settings()): what ifc_volume, ifc_footprint, ifc_distance and ifc_draw read is the element as built. The
    bounding boxes (reading._boxes, ifc_extent) keep their own fast setting. A mesh is kept under the setting it was
    made with."""
    import ifcopenshell.geom

    out: dict[int, Mesh] = {}
    todo = []
    for e in elements:
        kept = _cache.get((id(model), cut_openings, e.id()))
        if kept is not None:
            out[e.id()] = kept
        elif e.is_a("IfcProduct") and getattr(e, "Representation", None) and not e.is_a("IfcOpeningElement"):
            todo.append(e)
    if not todo:
        return out, False
    settings = mesh_settings(cut_openings)
    for first in range(0, len(todo), reading.GEOMETRY_BATCH):
        if time.monotonic() > deadline:
            return out, True
        it = ifcopenshell.geom.iterator(settings, model, 1, include=todo[first : first + reading.GEOMETRY_BATCH], geometry_library=reading.GEOMETRY_LIBRARY)
        if not it.initialize():
            continue
        while True:
            shape = it.get()
            verts = np.asarray(shape.geometry.verts, dtype=np.float64).reshape(-1, 3)
            faces = np.asarray(shape.geometry.faces, dtype=np.int32).reshape(-1, 3)
            if len(verts) and len(faces):
                mesh = Mesh(shape.id, verts, faces, cut_openings)
                out[shape.id] = mesh
                _keep(model, mesh)
            more = it.next()
            if more and time.monotonic() > deadline:
                del it
                return out, True
            if not more:
                break
        del it
    return out, False


# ---------------------------------------------------------------- choosing elements


def choose(model: ifcopenshell.file, selector: str = "", element_ids: str | list[int | str] = "", element_id: int | str = 0) -> tuple[list[ifcopenshell.entity_instance], str]:
    """The elements a call is about: a selector, a list of elements, one element, or several of these.

    ``element_ids`` is step ids or GlobalIds, separated by commas or spaces, such as
    ``"1203, 1247"`` or ``"2N1qx4tVn5RQ7hPZbG0Kd8"``, or a list of them (``[1203, 1247]``);
    ``element_id`` is one, either way. Returns the elements, sorted by id, and how the
    call named them."""
    found: dict[int, ifcopenshell.entity_instance] = {}
    said: list[str] = []
    if element_id:
        e = reading.element(model, element_id)
        found[e.id()] = e
        said.append(f"#{e.id()}")
    text = ", ".join(str(t) for t in element_ids) if isinstance(element_ids, list) else str(element_ids or "")
    for token in [t for t in text.replace(",", " ").split() if t]:
        e = reading.element(model, token)
        found[e.id()] = e
    if text.strip():
        said.append(f"element_ids {text.strip()}")
    if selector.strip():
        for e in ifcopenshell.util.selector.filter_elements(model, selector):
            found[e.id()] = e
        said.append(selector.strip())
    if not said:
        raise ValueError("Name what to measure: `selector` (such as 'IfcSlab, location = \"Level 1\"'), `element_ids` (step ids or GlobalIds), or `element_id`.")
    return [found[k] for k in sorted(found)], " + ".join(said)


# ---------------------------------------------------------------- citing a number

_sha_cache: dict[tuple[str, int, int], str] = {}


def file_sha256(path: str | None) -> str | None:
    """The sha256 of the model file at `path`, as it is on disk now (kept per size and time)."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


#: How many GlobalIds one reply cites before it says the rest by count.
MAX_CITED_IDS = 200


def cite(model: ifcopenshell.file, model_path: str | None, elements: list[ifcopenshell.entity_instance], asked: str, unit: str, method: str, edits: int) -> dict[str, Any]:
    """What flo2-calc needs to keep a measured number with its source."""
    guids = [e.GlobalId for e in elements if getattr(e, "GlobalId", None)]
    out: dict[str, Any] = {
        "model_file": os.path.basename(model_path) if model_path else None,
        "sha256": file_sha256(model_path),
        "elements_asked": asked,
        "global_ids": guids[:MAX_CITED_IDS],
        "unit": unit,
        "method": method,
        "measured_by": f"flo2-ifc {_version()}, ifcopenshell {ifcopenshell.version}, geometry library {reading.GEOMETRY_LIBRARY}",
    }
    if len(guids) > MAX_CITED_IDS:
        out["global_ids_withheld"] = len(guids) - MAX_CITED_IDS
        out["global_ids_note"] = f"{len(guids)} elements; the first {MAX_CITED_IDS} GlobalIds are listed. `elements_asked` names them all."
    if not model_path:
        out["note"] = "This model was made in the session (ifc_new) and has no file; cite it as such."
    elif edits:
        out["edited"] = (
            f"The session's copy was changed {edits} time(s) after it was opened, so this number is of the EDITED copy, not of the file "
            "the sha256 names. Save the copy and measure the saved file to cite it."
        )
    return out


def _version() -> str:
    from flo2_ifc import __version__

    return __version__


# ---------------------------------------------------------------- units of the answer


class Units:
    """The model's length, area and volume units, and how to say a value in each."""

    def __init__(self, model: ifcopenshell.file):
        u = reading.units(model)
        self.say = u["say"]
        self.length = u["length"]["symbol"] or "model units"
        self.length_name = reading._plural(u["length"]["name"]) or "model units"
        self.scale = reading.unit_scale(model)  # metres per model length unit
        self.decimals = reading._decimals_for(self.scale)
        area, volume = u["area"], u["volume"]
        self.area = area["symbol"] or f"{self.length}2"
        self.area_si = area["si_per_unit"] if area["declared"] and area["si_per_unit"] else self.scale**2
        self.volume = volume["symbol"] or f"{self.length}3"
        self.volume_si = volume["si_per_unit"] if volume["declared"] and volume["si_per_unit"] else self.scale**3

    def length_of(self, metres: float) -> dict[str, Any]:
        return {"model": round(metres / self.scale, self.decimals), "unit": self.length, "metres": round(metres, 4), "feet_inches": reading.feet_and_inches(metres)}

    def area_of(self, sq_metres: float) -> dict[str, Any]:
        return {"model": round(sq_metres / self.area_si, 4), "unit": self.area, "square_metres": round(sq_metres, 4), "square_feet": round(sq_metres / 0.09290304, 2)}

    def volume_of(self, cu_metres: float) -> dict[str, Any]:
        return {"model": round(cu_metres / self.volume_si, 6), "unit": self.volume, "cubic_metres": round(cu_metres, 6), "cubic_feet": round(cu_metres / 0.028316846592, 3)}

    def to_metres(self, value: float) -> float:
        return float(value) * self.scale


# ---------------------------------------------------------------- closed or open

#: Two corners closer than this (metres) are one corner when edges are matched.
WELD = 1e-4
#: An open mesh whose openings enclose no area, relative to its surface, is
#: closed at its seams: its triangles meet off each other's corners (a
#: T-junction), so some edges have no twin, but there is no hole, and its
#: volume is the same from wherever it is taken.
SEAM_RATIO = 1e-6


_closures: dict[tuple[int, bool, int], dict[str, Any]] = {}


def closure_of(model: ifcopenshell.file, mesh: Mesh) -> dict[str, Any]:
    """closure(mesh), kept with the mesh for the session's generation."""
    key = (id(model), mesh.openings_cut, mesh.id)
    if _cache.get(key) is not mesh:
        return closure(mesh)
    if key not in _closures:
        _closures[key] = closure(mesh)
    return _closures[key]


def closure(mesh: Mesh) -> dict[str, Any]:
    """Whether a mesh is closed: every edge shared by exactly two triangles.

    ``status`` is ``closed``, ``closed_at_seams`` (some edges have no twin,
    but the openings enclose no area) or ``open``. ``open_edges`` counts the
    edges with no twin, ``shared_by_more`` those shared by more than two."""
    v, f = mesh.verts, mesh.faces
    key = np.round(v / WELD).astype(np.int64)
    key -= key.min(axis=0)
    span = key.max(axis=0) + 1
    # One integer per welded corner, and one per edge: np.unique on rows is
    # several times slower than on a flat array.
    if float(span[0]) * float(span[1]) * float(span[2]) < 2.0**62:
        _, inv = np.unique((key[:, 0] * span[1] + key[:, 1]) * span[2] + key[:, 2], return_inverse=True)
    else:  # a shape kilometres across: too many places for one integer each
        _, inv = np.unique(key, axis=0, return_inverse=True)
    fw = inv.reshape(-1)[f].astype(np.int64)
    edges = np.sort(np.concatenate([fw[:, [0, 1]], fw[:, [1, 2]], fw[:, [2, 0]]]), axis=1)
    edges = edges[edges[:, 0] != edges[:, 1]]
    _, counts = np.unique(edges[:, 0] * (int(fw.max()) + 1) + edges[:, 1], return_counts=True)
    open_edges = int((counts == 1).sum())
    more = int((counts > 2).sum())
    tri = mesh.triangles
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]) / 2
    surface = float(np.linalg.norm(cross, axis=1).sum())
    gap = float(np.linalg.norm(cross.sum(axis=0)))
    ratio = gap / surface if surface else 0.0
    if open_edges == 0 and more == 0:
        status = "closed"
    elif ratio < SEAM_RATIO:
        status = "closed_at_seams"
    else:
        status = "open"
    return {"status": status, "open_edges": open_edges, "shared_by_more": more, "gap_ratio": float(f"{ratio:.3g}"), "surface_m2": surface}


def signed_volume(mesh: Mesh) -> float:
    """The mesh's signed volume in cubic metres, taken from its lowest corner."""
    tri = mesh.triangles - mesh.verts.min(axis=0)
    return float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)


# ---------------------------------------------------------------- footprints


#: Plan coordinates are snapped to this grid (metres) when triangles are joined.
GRID = 1e-4


def plan_polygon(triangles_xy: np.ndarray):
    """The union of 2D triangles ((m, 3, 2), metres), dropping any of no area."""
    import shapely

    if len(triangles_xy) == 0:
        return shapely.Polygon()
    t = triangles_xy
    area = 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))
    t = t[area > GRID * GRID]
    if len(t) == 0:
        return shapely.Polygon()
    return shapely.union_all(shapely.polygons(t), grid_size=GRID)


def footprint_of(mesh: Mesh):
    """An element's footprint: its triangles projected onto plan, joined."""
    return plan_polygon(mesh.triangles[:, :, :2])


def footprint(
    model: ifcopenshell.file,
    model_path: str | None = None,
    selector: str = "",
    element_ids: str | list[int | str] = "",
    element_id: int | str = 0,
    offset: int = 0,
    limit: int = 50,
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """How much plan area elements cover: each element's footprint, and the set's.

    Each footprint is the union of the element's triangles projected onto plan
    (x, y), so it is the ground the element covers whatever its shape. The
    set's footprint (``footprint``) is the union of those, so what is stacked
    is counted once; ``sum_of_elements`` adds each one's own, and
    ``stacked_overlap`` is the difference. Reads only."""
    import shapely

    offset, limit = reading._check_page(offset, limit)
    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    targets, asked = choose(model, selector, element_ids, element_id)
    got, stopped = meshes(model, targets, started + budget)
    u = Units(model)
    rows = []
    polys = []
    for e in targets:
        mesh = got.get(e.id())
        if mesh is None:
            continue
        if time.monotonic() > started + budget:
            stopped = True
            break
        p = footprint_of(mesh)
        polys.append(p)
        row = reading._brief(e)
        row["global_id"] = getattr(e, "GlobalId", None)
        row["footprint"] = u.area_of(p.area)
        rows.append(row)
    union = shapely.union_all(polys, grid_size=GRID) if polys else shapely.Polygon()
    total = sum(p.area for p in polys)
    out: dict[str, Any] = {
        "asked": asked,
        "matched": len(targets),
        "measured": len(rows),
        "no_shape": sum(1 for e in targets if e.id() not in got),
        "footprint": u.area_of(union.area),
        "sum_of_elements": u.area_of(total),
        "stacked_overlap": u.area_of(max(0.0, total - union.area)),
    }
    if not union.is_empty:
        minx, miny, maxx, maxy = union.bounds
        out["plan_box"] = {"min": [u.length_of(minx)["model"], u.length_of(miny)["model"]], "max": [u.length_of(maxx)["model"], u.length_of(maxy)["model"]], "unit": u.length}
        out["pieces"] = len(getattr(union, "geoms", [union]))
    page, info = reading.page_of(rows, offset, limit, chars=reading.PAGE_CHARS // 2)
    out["elements"] = page
    out["page"] = info
    how = reading._how_more("ifc_footprint", info, " (the same elements)")
    if how:
        out["more"] = how
    if stopped:
        out["partial"] = True
        out["stopped"] = f"Stopped after {budget:.0f} s with {len(rows)} of {len(targets)} elements measured, so the totals cover only those. Narrow the selector to measure the rest."
    out["cite"] = cite(
        model,
        model_path,
        [e for e in targets if any(r["id"] == e.id() for r in rows)],
        asked,
        u.area,
        "Footprint: each element's triangle mesh (IfcOpenShell's geometry, world coordinates) projected onto plan and joined (a union, to 0.1 mm), "
        "so stacked parts count once; the set's footprint is the union of the elements'.",
        edits,
    )
    out["seconds"] = round(time.monotonic() - started, 1)
    out["units"] = u.say
    return out


# ---------------------------------------------------------------- volumes


def volume(
    model: ifcopenshell.file,
    model_path: str | None = None,
    selector: str = "",
    element_ids: str | list[int | str] = "",
    element_id: int | str = 0,
    offset: int = 0,
    limit: int = 50,
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """How much solid elements hold, from their meshes, and which meshes are not closed.

    A volume is given only for a CLOSED mesh (every edge shared by two
    triangles), or one closed at its seams (some edges have no twin, but the
    openings enclose no area). An OPEN mesh has no solid volume: what its
    triangles enclose depends on where it is measured from. It is listed in
    ``not_closed`` with how open it is, and left out of ``total``; its stored
    quantity, if the model has one, is not checked here. Reads only."""
    offset, limit = reading._check_page(offset, limit)
    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    targets, asked = choose(model, selector, element_ids, element_id)
    got, stopped = meshes(model, targets, started + budget)
    u = Units(model)
    rows: list[dict[str, Any]] = []
    not_closed: list[dict[str, Any]] = []
    total = 0.0
    counted: list[ifcopenshell.entity_instance] = []
    for e in targets:
        mesh = got.get(e.id())
        if mesh is None:
            continue
        c = closure(mesh)
        row = reading._brief(e)
        row["global_id"] = getattr(e, "GlobalId", None)
        row["mesh"] = c["status"]
        if c["status"] == "open":
            lo, hi = mesh.verts.min(axis=0), mesh.verts.max(axis=0)
            row["volume"] = None
            row["why_no_volume"] = (
                f"its mesh is not closed: {c['open_edges']} edge(s) have no twin and {c['shared_by_more']} are shared by more than two triangles, "
                f"and its openings enclose {c['gap_ratio']:.2g} of its surface"
            )
            row["box_volume_at_most"] = u.volume_of(float(np.prod(hi - lo)))
            not_closed.append({k: row[k] for k in ("id", "type", "name", "global_id") if k in row} | {"open_edges": c["open_edges"], "shared_by_more": c["shared_by_more"]})
        else:
            v = abs(signed_volume(mesh))
            row["volume"] = u.volume_of(v)
            if c["status"] == "closed_at_seams":
                row["note"] = f"closed at its seams: {c['open_edges']} edge(s) have no twin, but they enclose no area, so the volume does not depend on where it is taken from"
            total += v
            counted.append(e)
        rows.append(row)
    out: dict[str, Any] = {
        "asked": asked,
        "matched": len(targets),
        "measured": len(rows),
        "no_shape": sum(1 for e in targets if e.id() not in got),
        "total": u.volume_of(total),
        "total_of": f"{len(counted)} element(s) with a closed mesh" + (f"; {len(not_closed)} with an open mesh are NOT in it" if not_closed else ""),
    }
    if not_closed:
        out["not_closed"] = not_closed[:100]
        out["not_closed_count"] = len(not_closed)
        out["not_closed_note"] = (
            "These meshes are open, so they hold no solid volume a number could quote: what an open mesh encloses depends on where it is measured from. "
            "A volume the model STORES for them (ifc_info, Qto_*) was computed by the exporter and may share the fault: check it against ifc_extent's box, "
            "which no solid can exceed."
        )
    page, info = reading.page_of(rows, offset, limit, chars=reading.PAGE_CHARS // 2)
    out["elements"] = page
    out["page"] = info
    how = reading._how_more("ifc_volume", info, " (the same elements)")
    if how:
        out["more"] = how
    if stopped:
        out["partial"] = True
        out["stopped"] = f"Stopped after {budget:.0f} s with {len(got)} of {len(targets)} shapes made, so the total covers only those. Narrow the selector to measure the rest."
    out["cite"] = cite(
        model,
        model_path,
        counted,
        asked,
        u.volume,
        "Volume: the enclosed volume of each element's closed triangle mesh (IfcOpenShell's geometry, world coordinates; the divergence theorem), "
        "summed; an element whose mesh is open is named and not summed.",
        edits,
    )
    out["seconds"] = round(time.monotonic() - started, 1)
    out["units"] = u.say
    return out


# ---------------------------------------------------------------- distance


def _closest_on_triangles(p: np.ndarray, tri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each point p[i] and each triangle tri[j]: the closest point on the
    triangle and the squared distance. Shapes (n, m, 3) and (n, m). Ericson,
    Real-Time Collision Detection, 5.1.5."""
    a, b, c = tri[None, :, 0], tri[None, :, 1], tri[None, :, 2]
    p = p[:, None, :]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp = p - b
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    cp = p - c
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = va + vb + vc
        v = np.where(denom != 0, vb / denom, 0.0)
        w = np.where(denom != 0, vc / denom, 0.0)
        q = a + ab * v[..., None] + ac * w[..., None]  # inside the face
        # edge regions
        t_ab = np.clip(np.where(d1 - d3 != 0, d1 / (d1 - d3), 0.0), 0, 1)
        t_ac = np.clip(np.where(d2 - d6 != 0, d2 / (d2 - d6), 0.0), 0, 1)
        t_bc = np.clip(np.where((d4 - d3) + (d5 - d6) != 0, (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0), 0, 1)
    q_ab = a + ab * t_ab[..., None]
    q_ac = a + ac * t_ac[..., None]
    q_bc = b + (c - b) * t_bc[..., None]
    region_a = (d1 <= 0) & (d2 <= 0)
    region_b = (d3 >= 0) & (d4 <= d3)
    region_c = (d6 >= 0) & (d5 <= d6)
    region_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    region_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    region_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    q = np.where(region_bc[..., None], q_bc, q)
    q = np.where(region_ac[..., None], q_ac, q)
    q = np.where(region_ab[..., None], q_ab, q)
    q = np.where(region_c[..., None], np.broadcast_to(c, q.shape), q)
    q = np.where(region_b[..., None], np.broadcast_to(b, q.shape), q)
    q = np.where(region_a[..., None], np.broadcast_to(a, q.shape), q)
    return q, ((q - p) ** 2).sum(-1)


def _segments_closest(p1: np.ndarray, q1: np.ndarray, p2: np.ndarray, q2: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Closest points between every segment p1q1[i] and p2q2[j]: (n, m, 3) twice and the squared distance (n, m). Ericson 5.1.9."""
    d1 = (q1 - p1)[:, None, :]
    d2 = (q2 - p2)[None, :, :]
    r = p1[:, None, :] - p2[None, :, :]
    a = (d1 * d1).sum(-1)
    e = (d2 * d2).sum(-1)
    f = (d2 * r).sum(-1)
    c = (d1 * r).sum(-1)
    b = (d1 * d2).sum(-1)
    denom = a * e - b * b
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(denom > 1e-18, np.clip((b * f - c * e) / denom, 0, 1), 0.0)
        t = np.where(e > 1e-18, (b * s + f) / e, 0.0)
        s = np.where(t < 0, np.where(a > 1e-18, np.clip(-c / a, 0, 1), 0.0), np.where(t > 1, np.where(a > 1e-18, np.clip((b - c) / a, 0, 1), 0.0), s))
    t = np.clip(t, 0, 1)
    c1 = p1[:, None, :] + d1 * s[..., None]
    c2 = p2[None, :, :] + d2 * t[..., None]
    return c1, c2, ((c1 - c2) ** 2).sum(-1)


def _segments_hit_triangles(p: np.ndarray, q: np.ndarray, tri: np.ndarray) -> np.ndarray | None:
    """A point where some segment p[i]q[i] passes through some triangle, or None (Moller-Trumbore)."""
    d = (q - p)[:, None, :]
    a, b, c = tri[None, :, 0], tri[None, :, 1], tri[None, :, 2]
    e1, e2 = b - a, c - a
    h = np.cross(d, e2)
    det = (e1 * h).sum(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(np.abs(det) > 1e-18, 1.0 / det, 0.0)
        s = p[:, None, :] - a
        uu = inv * (s * h).sum(-1)
        qq = np.cross(s, e1)
        vv = inv * (d * qq).sum(-1)
        tt = inv * (e2 * qq).sum(-1)
    hit = (np.abs(det) > 1e-18) & (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (tt >= 0) & (tt <= 1)
    if not hit.any():
        return None
    i, j = np.argwhere(hit)[0]
    return p[i] + (q[i] - p[i]) * tt[i, j]


def _edges(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    f = mesh.faces
    e = np.unique(np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1), axis=0)
    return mesh.verts[e[:, 0]], mesh.verts[e[:, 1]]


#: Pairs one chunk of the distance search works on at once (memory: about 200 bytes a pair).
_CHUNK = 200_000
#: The most point-triangle and edge-edge pairs one distance call compares.
MAX_PAIRS = int(os.environ.get("FLO2_IFC_DISTANCE_PAIRS", "60000000"))


def _chunked(n: int, m: int) -> int:
    return max(1, _CHUNK // max(1, m))


def surface_distance(a: Mesh, b: Mesh, deadline: float) -> dict[str, Any]:
    """The shortest distance between two meshes' surfaces, in metres, with the two closest points.

    Exact for triangles: every corner of each against every triangle of the
    other, every edge against every edge, and whether any edge passes through
    the other's triangles (then they touch or overlap, distance 0)."""
    ta, tb = a.triangles, b.triangles
    ea, eb = _edges(a), _edges(b)
    pairs = len(a.verts) * len(tb) + len(b.verts) * len(ta) + len(ea[0]) * len(eb[0]) + len(ea[0]) * len(tb) + len(eb[0]) * len(ta)
    if pairs > MAX_PAIRS:
        raise ValueError(
            f"These two shapes are too detailed to compare exactly here ({pairs:,} pairs of corners, edges and triangles, at most {MAX_PAIRS:,}); "
            "ifc_extent gives the gap between their boxes."
        )
    for p, q, tri in ((ea[0], ea[1], tb), (eb[0], eb[1], ta)):
        for i in range(0, len(p), _chunked(len(p), len(tri))):
            hit = _segments_hit_triangles(p[i : i + _chunked(len(p), len(tri))], q[i : i + _chunked(len(p), len(tri))], tri)
            if hit is not None:
                return {"metres": 0.0, "touching": True, "point_a": hit.tolist(), "point_b": hit.tolist()}
            if time.monotonic() > deadline:
                raise TimeoutError
    best = (math.inf, None, None)
    for pts, tri, swap in ((a.verts, tb, False), (b.verts, ta, True)):
        step = _chunked(len(pts), len(tri))
        for i in range(0, len(pts), step):
            q, d2 = _closest_on_triangles(pts[i : i + step], tri)
            k = np.unravel_index(np.argmin(d2), d2.shape)
            if d2[k] < best[0]:
                p0, q0 = pts[i + k[0]], q[k]
                best = (float(d2[k]), q0 if swap else p0, p0 if swap else q0)
            if time.monotonic() > deadline:
                raise TimeoutError
    step = _chunked(len(ea[0]), len(eb[0]))
    for i in range(0, len(ea[0]), step):
        c1, c2, d2 = _segments_closest(ea[0][i : i + step], ea[1][i : i + step], eb[0], eb[1])
        k = np.unravel_index(np.argmin(d2), d2.shape)
        if d2[k] < best[0]:
            best = (float(d2[k]), c1[k], c2[k])
        if time.monotonic() > deadline:
            raise TimeoutError
    return {"metres": math.sqrt(best[0]), "touching": best[0] < 1e-12, "point_a": np.asarray(best[1]).tolist(), "point_b": np.asarray(best[2]).tolist()}


def distance(
    model: ifcopenshell.file,
    model_path: str | None = None,
    element_id: int | str = "",
    other_element_id: int | str = "",
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """The distance between two elements: the shortest gap between their surfaces, with the two closest points.

    Also the gap between their boxes along each axis (0 where they overlap on
    that axis), the distance between their boxes' centres, and in plan.
    ``element_id`` and ``other_element_id`` are each one element: its step id or its
    GlobalId; the reply calls them ``a`` and ``b``. Reads only."""
    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    if str(element_id).strip() in ("", "0") or str(other_element_id).strip() in ("", "0"):
        raise ValueError("Name exactly one element in `element_id` and one in `other_element_id`: each by its step id or its GlobalId.")
    ea, eb = reading.element(model, element_id), reading.element(model, other_element_id)
    got, stopped = meshes(model, [ea, eb], started + budget)
    missing = [e for e in (ea, eb) if e.id() not in got]
    if missing:
        raise ValueError(f"#{missing[0].id()} {missing[0].is_a()} has no 3D shape to measure from." if not stopped else "Time ran out before the two shapes were made.")
    ma, mb = got[ea.id()], got[eb.id()]
    u = Units(model)
    try:
        d = surface_distance(ma, mb, started + budget)
    except TimeoutError:
        raise ValueError(f"The exact distance did not finish within {budget:.0f} s; ifc_extent gives the gap between their boxes.") from None
    lo_a, hi_a = ma.verts.min(0), ma.verts.max(0)
    lo_b, hi_b = mb.verts.min(0), mb.verts.max(0)
    gaps = np.maximum(0.0, np.maximum(lo_a - hi_b, lo_b - hi_a))
    ca, cb = (lo_a + hi_a) / 2, (lo_b + hi_b) / 2
    to_model = lambda p: [round(v / u.scale, u.decimals) for v in p]  # noqa: E731
    out: dict[str, Any] = {
        "a": reading._brief(ea) | {"global_id": ea.GlobalId},
        "b": reading._brief(eb) | {"global_id": eb.GlobalId},
        "clear_distance": u.length_of(d["metres"]),
        "touching_or_overlapping": d["touching"],
        "closest_points": {"on_a": to_model(d["point_a"]), "on_b": to_model(d["point_b"]), "unit": u.length},
        "box_gap": {"x": u.length_of(float(gaps[0])), "y": u.length_of(float(gaps[1])), "z": u.length_of(float(gaps[2]))},
        "centre_to_centre": u.length_of(float(np.linalg.norm(cb - ca))),
        "centre_to_centre_in_plan": u.length_of(float(np.linalg.norm((cb - ca)[:2]))),
        "centre_height_difference": u.length_of(float(cb[2] - ca[2])),
    }
    out["cite"] = cite(
        model,
        model_path,
        [ea, eb],
        f"element_id = {str(element_id).strip()}, other_element_id = {str(other_element_id).strip()}",
        u.length,
        "Distance: the shortest distance between the two elements' triangle meshes (IfcOpenShell's geometry, world coordinates), "
        "exact over every corner, edge and triangle; box gaps and centres from the meshes' axis-aligned boxes.",
        edits,
    )
    out["seconds"] = round(time.monotonic() - started, 1)
    out["units"] = u.say
    return out

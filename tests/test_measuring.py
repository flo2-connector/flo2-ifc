"""The measures (src/flo2_ifc/geometry.py), on a synthetic model whose sizes are known.

A chat measures a building model (req:a-chat-measures-and-draws-a-designs-building-model):
a footprint with stacked items counted once, a volume that refuses an open
mesh, the height above a datum, and the distance between two elements, each
with what a calculation needs to cite it. No real model is a fixture here
(tests/models.py, measured_site and SITE).
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from flo2_ifc import geometry as G
from flo2_ifc import reading as R
from tests import models


@pytest.fixture(scope="module")
def site():
    return models.measured_site()


@pytest.fixture(scope="module")
def saved(tmp_path_factory, site):
    path = tmp_path_factory.mktemp("site") / "site.ifc"
    site.write(str(path))
    return str(path)


def ids_of(model, cls, name=None):
    return [e.id() for e in model.by_type(cls) if name is None or e.Name == name]


# ------------------------------------------------------------ footprint


def test_a_set_footprint_counts_what_is_stacked_once(site):
    out = G.footprint(site, selector="IfcFooting, IfcSlab")
    assert out["footprint"]["square_metres"] == 24.0  # the slab covers the footing
    assert out["sum_of_elements"]["square_metres"] == 40.0
    assert out["stacked_overlap"]["square_metres"] == 16.0
    assert out["footprint"]["unit"] == "m2" and out["footprint"]["square_feet"] == pytest.approx(258.33, abs=0.01)
    assert out["plan_box"] == {"min": [0.0, 0.0], "max": [6000.0, 4000.0], "unit": "mm"}


def test_an_upright_wall_has_the_footprint_of_its_base(site):
    out = G.footprint(site, element_id=ids_of(site, "IfcWall", "Wall A")[0])
    assert out["footprint"]["square_metres"] == pytest.approx(0.8)  # 4 x 0.2 m, its sides add nothing


def test_elements_are_named_by_ids_or_global_ids(site):
    a, b = site.by_type("IfcWall")
    by_ids = G.footprint(site, ids=f"{a.id()}, {b.id()}")
    by_guid = G.footprint(site, ids=f"{a.GlobalId} {b.GlobalId}")
    assert by_ids["footprint"] == by_guid["footprint"]
    assert by_ids["footprint"]["square_metres"] == pytest.approx(1.6)
    with pytest.raises(ValueError, match="No element has the id or GlobalId"):
        G.footprint(site, ids="0zzzzzzzzzzzzzzzzzzzzz")


def test_a_footprint_cites_its_file_elements_unit_and_method(site, saved):
    import ifcopenshell

    model = ifcopenshell.open(saved)
    out = G.footprint(model, saved, selector="IfcSlab")
    c = out["cite"]
    assert c["model_file"] == "site.ifc"
    assert c["sha256"] == G.file_sha256(saved) and len(c["sha256"]) == 64
    assert c["global_ids"] == [model.by_type("IfcSlab")[0].GlobalId]
    assert c["unit"] == "m2" and "union" in c["method"]
    assert "edited" not in c
    assert "EDITED" in G.footprint(model, saved, selector="IfcSlab", edits=2)["cite"]["edited"]


def test_a_long_list_is_paged(site):
    out = G.footprint(site, selector="IfcBuildingElement", limit=2)
    assert out["page"]["listed"] == 2 and out["page"]["next_offset"] == 2
    assert "more" in out
    assert out["footprint"]["square_metres"] > 0  # the set's total covers every element, not just the page


# ------------------------------------------------------------ volume


def test_a_closed_mesh_has_a_volume_and_an_open_one_is_named_and_refused(site):
    out = G.volume(site, selector="IfcBuildingElementProxy")
    rows = {r["name"]: r for r in out["elements"]}
    assert rows["Closed box"]["mesh"] == "closed"
    assert rows["Closed box"]["volume"]["cubic_metres"] == pytest.approx(6.0)
    assert rows["Open box"]["mesh"] == "open" and rows["Open box"]["volume"] is None
    assert "not closed" in rows["Open box"]["why_no_volume"]
    assert rows["Open box"]["box_volume_at_most"]["cubic_metres"] == pytest.approx(6.0)
    assert out["total"]["cubic_metres"] == pytest.approx(6.0)  # the open box is NOT in it
    assert [n["name"] for n in out["not_closed"]] == ["Open box"]
    assert "1 with an open mesh are NOT in it" in out["total_of"]
    assert out["cite"]["global_ids"] == [site.by_id(rows["Closed box"]["id"]).GlobalId]


def test_extruded_solids_are_closed(site):
    out = G.volume(site, selector="IfcWall, IfcSlab, IfcFooting")
    assert out["total"]["cubic_metres"] == pytest.approx(2.4 + 2.4 + 4.8 + 8.0)
    assert "not_closed" not in out


def test_an_open_mesh_volume_depends_on_where_it_is_taken_from():
    # Why an open mesh is refused: the same open box, moved, encloses a different "volume".
    v, t = models._box(0, 0, 0, 1, 2, 3, top=False)
    verts, faces = np.array(v, float), np.array(t, np.int32)
    vol = lambda shift: float(np.einsum("ij,ij->i", (verts + shift)[faces][:, 0], np.cross((verts + shift)[faces][:, 1], (verts + shift)[faces][:, 2])).sum() / 6)  # noqa: E731
    assert abs(vol(0) - vol(np.array([0, 0, 10.0]))) > 1.0
    assert G.closure(G.Mesh(1, verts, faces))["status"] == "open"


def test_a_mesh_closed_at_its_seams_is_closed_in_effect():
    # A box whose bottom is split at a point on one edge's middle (a T-junction): some
    # edges have no twin, but nothing is open, so the volume is the same from anywhere.
    v, t = models._box(0, 0, 0, 2, 1, 1)
    v = v + [(1.0, 0.0, 0.0)]  # the middle of the bottom front edge (0 -> 1)
    t = [tri for tri in t if tri not in ((0, 2, 3), (0, 3, 1))] + [(0, 2, 8), (8, 2, 3), (8, 3, 1)]
    mesh = G.Mesh(1, np.array(v, float), np.array(t, np.int32))
    c = G.closure(mesh)
    assert c["open_edges"] > 0 and c["status"] == "closed_at_seams"
    assert abs(G.signed_volume(mesh)) == pytest.approx(2.0)


# ------------------------------------------------------------ extent and datum


def test_height_above_a_storey(site):
    out = R.extent(site, selector="IfcWall", datum="FIRST")
    assert out["above_datum"]["datum_z"] == 3000.0
    assert out["above_datum"]["top"]["model"] == 200.0  # the walls reach 3.2 m, 0.2 m above FIRST
    assert out["above_datum"]["bottom"]["metres"] == -2.8
    assert out["elements"][0]["above_datum"] == {"bottom": -2800.0, "top": 200.0}
    assert out["cite"]["unit"] == "mm" and len(out["cite"]["global_ids"]) == 2


def test_height_above_a_number(site):
    out = R.extent(site, selector="IfcSlab", datum="-500")
    assert out["above_datum"]["top"]["model"] == 700.0


# ------------------------------------------------------------ distance


def test_the_clear_distance_between_two_walls(site):
    a, b = ids_of(site, "IfcWall", "Wall A")[0], ids_of(site, "IfcWall", "Wall B")[0]
    out = G.distance(site, a=str(a), b=site.by_id(b).GlobalId)
    assert out["clear_distance"]["model"] == pytest.approx(1800.0)
    assert out["clear_distance"]["feet_inches"] == "5' 10 7/8\""
    assert out["touching_or_overlapping"] is False
    assert out["box_gap"]["y"]["metres"] == pytest.approx(1.8) and out["box_gap"]["x"]["metres"] == 0.0
    assert out["centre_to_centre"]["metres"] == pytest.approx(2.0)
    assert out["closest_points"]["on_a"][1] == pytest.approx(200.0) and out["closest_points"]["on_b"][1] == pytest.approx(2000.0)
    assert len(out["cite"]["global_ids"]) == 2


def test_elements_that_overlap_are_zero_apart(site):
    out = G.distance(site, a=str(ids_of(site, "IfcSlab")[0]), b=str(ids_of(site, "IfcWall", "Wall A")[0]))
    assert out["clear_distance"]["metres"] == 0.0 and out["touching_or_overlapping"] is True


def test_distance_needs_one_element_each_side(site):
    with pytest.raises(ValueError, match="exactly one element"):
        G.distance(site, a="", b="1")


def test_surface_distance_is_exact_between_skew_triangles():
    a = G.Mesh(1, np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float), np.array([[0, 1, 2]], np.int32))
    b = G.Mesh(2, np.array([[0.2, 0.2, 1], [1, 0.2, 2], [0.2, 1, 2]], float), np.array([[0, 1, 2]], np.int32))
    d = G.surface_distance(a, b, time.monotonic() + 10)
    assert d["metres"] == pytest.approx(1.0)
    # An edge that pierces the other triangle: they touch.
    c = G.Mesh(3, np.array([[0.2, 0.2, -1], [0.2, 0.2, 1], [0.3, 0.2, 1]], float), np.array([[0, 1, 2]], np.int32))
    assert G.surface_distance(a, c, time.monotonic() + 10)["metres"] == 0.0


# ------------------------------------------------------------ the meshes are kept, and forgotten on a change


def test_meshes_are_kept_until_the_model_changes(site):
    G.changed()
    walls = site.by_type("IfcWall")
    first, _ = G.meshes(site, walls, time.monotonic() + 30)
    again, _ = G.meshes(site, walls, time.monotonic() + 30)
    assert all(first[k] is again[k] for k in first)
    G.changed()
    after, _ = G.meshes(site, walls, time.monotonic() + 30)
    assert all(first[k] is not after[k] for k in first)

"""What changed between two models (src/flo2_ifc/diffing.py), with IfcOpenShell's own IfcDiff, on synthetic models.

The model before is the synthetic house (tests/models.py), written to a file. The model after is that file opened
again and changed with IfcOpenShell's API, so both share every GlobalId and the edits are known exactly:

- wall 0 renamed (an attribute), wall 1 given a fire rating (a property), wall 2 moved 1 m east (its placement);
- the door deleted, and a column added.

What must hold: the exact GlobalIds added, deleted and changed, and what changed on each; the counts by class; the
whole comparison kept as a JSON file (with IfcDiff's own report in it); a long answer listed a page at a time with the
rest in the file; and every refusal in plain words, with nothing written: no second model, a file that is not IFC,
something IfcDiff cannot compare, two models too large for the helper's memory, and a comparison out of time. No
real model is a fixture here.
"""

from __future__ import annotations

import json
import os

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.placement
import numpy as np
import pytest

from flo2_ifc import diffing as D
from tests import models

run = ifcopenshell.api.run


@pytest.fixture()
def pair(tmp_path):
    """(the model before, open as a session holds it; its path; the path of the model after; the known edits)."""
    before_path = str(tmp_path / "house.ifc")
    models.building(walls=3).write(before_path)
    after = ifcopenshell.open(before_path)
    walls = sorted(after.by_type("IfcWall"), key=lambda w: w.Name)
    walls[0].Name = "Kitchen wall"
    pset = run("pset.add_pset", after, product=walls[1], name="Pset_WallCommon")
    run("pset.edit_pset", after, pset=pset, properties={"FireRating": "EI60"})
    moved = np.array(ifcopenshell.util.placement.get_local_placement(walls[2].ObjectPlacement))
    moved[0, 3] += 1000.0  # the house is in millimetres
    run("geometry.edit_object_placement", after, product=walls[2], matrix=moved, is_si=False)
    door = after.by_type("IfcDoor")[0]
    edits = {
        "renamed": walls[0].GlobalId,
        "rated": walls[1].GlobalId,
        "moved": walls[2].GlobalId,
        "deleted": door.GlobalId,
    }
    run("root.remove_product", after, product=door)
    column = run("root.create_entity", after, ifc_class="IfcColumn", name="New column")
    run("spatial.assign_container", after, products=[column], relating_structure=after.by_type("IfcBuildingStorey")[0])
    edits["added"] = column.GlobalId
    after_path = str(tmp_path / "house-after.ifc")
    after.write(after_path)
    return ifcopenshell.open(before_path), before_path, after_path, edits


def _diff(pair, tmp_path, **kw):
    model, before_path, after_path, _ = pair
    return D.diff(model, before_path, kw.pop("other", after_path), str(tmp_path / "changes"), **kw)


def test_the_exact_elements_added_deleted_and_changed(pair, tmp_path):
    out = _diff(pair, tmp_path)
    edits = pair[3]
    assert out["complete"] is True
    assert [e["global_id"] for e in out["added"]] == [edits["added"]]
    assert out["added"][0] == {"global_id": edits["added"], "class": "IfcColumn", "name": "New column"}
    assert [e["global_id"] for e in out["deleted"]] == [edits["deleted"]]
    assert out["deleted"][0]["class"] == "IfcDoor"
    changed = {e["global_id"]: e for e in out["changed"]}
    assert set(changed) == {edits["renamed"], edits["rated"], edits["moved"]}
    assert out["counts"] == {"before": 11, "after": 11, "added": 1, "deleted": 1, "changed": 3, "unchanged": 7}
    assert out["by_class"] == {"IfcColumn": {"added": 1, "deleted": 0, "changed": 0}, "IfcDoor": {"added": 0, "deleted": 1, "changed": 0}, "IfcWall": {"added": 0, "deleted": 0, "changed": 3}}


def test_what_changed_on_each_element(pair, tmp_path):
    out = _diff(pair, tmp_path)
    edits = pair[3]
    changed = {e["global_id"]: e for e in out["changed"]}
    renamed = changed[edits["renamed"]]
    assert renamed["changed"] == ["attributes"]
    assert renamed["attributes"] == {"Name": {"before": "Wall 000", "after": "Kitchen wall"}}
    assert renamed["name"] == "Kitchen wall" and renamed["name_before"] == "Wall 000"
    rated = changed[edits["rated"]]
    assert rated["changed"] == ["properties"]
    assert rated["properties"] == {"dictionary_item_added": ["root['Pset_WallCommon']"]}, "IfcDiff's own record of the property change"
    moved = changed[edits["moved"]]
    assert moved["changed"] == ["placement", "geometry"], "its placement (IfcDiff's quick check) and its shape (its full one)"
    assert moved["placement"]["placement_changed"] is True
    assert moved["placement"]["placement_moved_by"] == [1000.0, 0.0, 0.0], "in the model's length unit, millimetres"


# Step 7 of flo2's owner's-change loop (2026-11-03, flo2-ifc's fact:root-cause-ifc-diffs-moved-by-is-the-
# placement-origins-move-not-the-elements-2026-11-03): on a Revit export of Autodesk's sample house, every
# level lowered 238.125 mm, `moved_by` read -476.25 mm for a microwave whose shape moved -238.1 mm, and no
# move for elements whose shapes moved; a chat read it as how far each element moved. It is where the
# element's PLACEMENT point went, which an IFC writer may move apart from the shape inside it.
def test_a_move_is_named_for_what_it_measures_the_placement_and_the_answer_says_how_to_measure_the_element(pair, tmp_path):
    out = _diff(pair, tmp_path)
    moved = {e["global_id"]: e for e in out["changed"]}[pair[3]["moved"]]
    assert "moved_by" not in moved["placement"], "no field named for a move it does not measure"
    assert moved["placement"]["placement_moved_by"] == [1000.0, 0.0, 0.0]
    assert out["placement_note"] == D.PLACEMENT_NOTE
    assert "placement point" in D.PLACEMENT_NOTE and "ifc_extent" in D.PLACEMENT_NOTE and "not always how far the element" in D.PLACEMENT_NOTE
    kept = json.loads((tmp_path / "changes.json").read_text())
    assert kept["placement_note"] == D.PLACEMENT_NOTE, "the kept file says it too"


def test_the_whole_comparison_is_kept_as_a_json_file(pair, tmp_path):
    out = _diff(pair, tmp_path)
    path = str(tmp_path / "changes.json")
    assert out["written"] == path
    with open(path, "rb") as f:
        data = f.read()
    import hashlib

    assert out["sha256"] == hashlib.sha256(data).hexdigest() and out["bytes"] == len(data)
    kept = json.loads(data)
    assert kept["format"] == "flo2-ifc ifc_diff 1"
    assert kept["before"]["file"] == "house.ifc" and kept["after"]["file"] == "house-after.ifc"
    assert len(kept["before"]["sha256"]) == 64 and len(kept["after"]["sha256"]) == 64
    assert kept["options"] == {"compare": list(D.DEFAULT_COMPARE), "is_shallow": False, "selector": None}
    assert kept["counts"] == out["counts"]
    assert [e["global_id"] for e in kept["changed"]] == [e["global_id"] for e in out["changed"]]
    # IfcDiff's own report, as its export writes it.
    edits = pair[3]
    assert kept["ifcdiff"]["added"] == [edits["added"]] and kept["ifcdiff"]["deleted"] == [edits["deleted"]]
    assert kept["ifcdiff"]["changed"][edits["renamed"]] == {"attributes_changed": True}
    assert kept["ifcdiff"]["changed"][edits["moved"]] == {"placement_changed": True, "geometry_changed": True}
    assert "IfcDiff 0.9.0" in kept["compared_by"]


def test_the_other_model_can_be_the_one_before(pair, tmp_path):
    model, before_path, after_path, edits = pair
    after = ifcopenshell.open(after_path)
    out = D.diff(after, after_path, before_path, str(tmp_path / "back"), other_is="before")
    assert [e["global_id"] for e in out["added"]] == [edits["added"]]
    assert [e["global_id"] for e in out["deleted"]] == [edits["deleted"]]
    assert out["before"]["file"] == "house.ifc" and out["after"]["file"] == "house-after.ifc"


def test_compare_and_selector_narrow_it(pair, tmp_path):
    edits = pair[3]
    only_attributes = _diff(pair, tmp_path, compare="attributes")
    assert [e["global_id"] for e in only_attributes["changed"]] == [edits["renamed"]]
    assert only_attributes["options"]["compare"] == ["attributes"]
    walls = _diff(pair, tmp_path, selector="IfcWall")
    assert walls["added"] == [] and walls["deleted"] == [] and walls["counts"]["changed"] == 3


def test_a_model_written_again_with_every_entity_renumbered_reads_unchanged(pair, tmp_path):
    """Another export of the same model numbers its entities differently. IfcDiff 0.9.0 means to leave a property
    set's STEP id out of the comparison, and its pattern for it matches no path DeepDiff 9 writes; flo2-ifc leaves it
    out, so a renumbered copy reads as what it is: unchanged."""
    import re

    model, before_path, _, _ = pair
    text = open(before_path).read()
    head, data = text.split("DATA;", 1)
    renumbered = tmp_path / "renumbered.ifc"
    renumbered.write_text(head + "DATA;" + re.sub(r"#(\d+)", lambda m: f"#{int(m.group(1)) + 100000}", data))
    out = D.diff(model, before_path, str(renumbered), str(tmp_path / "same"))
    assert out["counts"]["changed"] == 0, out["changed"]
    assert out["added"] == [] and out["deleted"] == []


def test_two_large_models_compare_placements_in_place_of_shapes(pair, tmp_path, monkeypatch):
    """Above GEOMETRY_MAX_BYTES IfcDiff's full shape check does not fit one call or the helper's memory (measured), so
    the default compares placements (IfcDiff's quick geometry check) and says so; asking for shapes is refused
    unless `selector` narrows it."""
    monkeypatch.setattr(D, "GEOMETRY_MAX_BYTES", 1000)
    edits = pair[3]
    out = _diff(pair, tmp_path)
    assert out["options"]["compare"] == list(D.DEFAULT_COMPARE_LARGE) and "geometry" not in out["options"]["compare"]
    assert "shapes were not compared" in out["note_geometry"]
    moved = {e["global_id"]: e for e in out["changed"]}[edits["moved"]]
    assert moved["changed"] == ["placement"] and moved["placement"]["placement_moved_by"] == [1000.0, 0.0, 0.0]
    with pytest.raises(D.DiffRefused, match=r"comparing the shapes of two models this size \(.* together\) does not fit this helper.*Nothing was opened"):
        _diff(pair, tmp_path, compare="attributes, geometry")
    narrowed = _diff(pair, tmp_path, compare="geometry", selector="IfcWall")
    assert {e["global_id"] for e in narrowed["changed"]} == {edits["moved"]}


def test_a_long_answer_is_listed_a_page_at_a_time_and_the_file_holds_it_all(pair, tmp_path):
    out = _diff(pair, tmp_path, limit=1)
    assert len(out["changed"]) == 1
    assert out["changed_page"]["total"] == 3 and out["changed_page"]["withheld"] == 2 and out["changed_page"]["next_offset"] == 1
    assert "kept file" in out["more"]
    kept = json.loads(open(out["written"]).read())
    assert len(kept["changed"]) == 3
    rest = _diff(pair, tmp_path, offset=1, limit=2)
    assert len(rest["changed"]) == 2


def test_its_prints_never_reach_the_servers_stdout(pair, tmp_path, capfd):
    """IfcDiff prints its progress; the server's stdout is the MCP stream, so nothing may reach it."""
    _diff(pair, tmp_path)
    assert capfd.readouterr().out == ""


# ---------------------------------------------------------------- refusals, each with nothing written


def _nothing_written(tmp_path):
    assert not os.path.exists(tmp_path / "changes.json")


def test_a_missing_second_model_is_refused(pair, tmp_path):
    with pytest.raises(D.DiffRefused, match="there is no model at"):
        _diff(pair, tmp_path, other=str(tmp_path / "nowhere.ifc"))
    _nothing_written(tmp_path)


def test_a_file_that_is_not_ifc_is_refused(pair, tmp_path):
    notes = tmp_path / "notes.ifc"
    notes.write_text("a list of what changed, not a model\n")
    with pytest.raises(D.DiffRefused, match="is not an IFC model"):
        _diff(pair, tmp_path, other=str(notes))
    _nothing_written(tmp_path)


def test_what_ifcdiff_cannot_compare_is_refused(pair, tmp_path):
    with pytest.raises(D.DiffRefused, match="IfcDiff compares attributes, property, type, container, aggregate, classification, placement, geometry"):
        _diff(pair, tmp_path, compare="colour")
    with pytest.raises(D.DiffRefused, match='"after"'):
        _diff(pair, tmp_path, other_is="sideways")
    _nothing_written(tmp_path)


def test_two_models_too_large_for_the_helpers_memory_are_refused_before_either_is_opened(pair, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MAX_BYTES", 1000)
    with pytest.raises(D.DiffRefused, match="too large to compare within this helper's memory.*Nothing was opened"):
        _diff(pair, tmp_path)
    _nothing_written(tmp_path)


def test_a_comparison_out_of_time_is_stopped_and_refused(pair, tmp_path):
    with pytest.raises(D.DiffRefused, match="did not finish within 0 s"):
        _diff(pair, tmp_path, seconds=0)
    _nothing_written(tmp_path)


def test_the_same_file_unedited_is_refused(pair, tmp_path):
    model, before_path, _, _ = pair
    with pytest.raises(D.DiffRefused, match="the same file"):
        D.diff(model, before_path, before_path, str(tmp_path / "changes"))

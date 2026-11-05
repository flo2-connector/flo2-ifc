"""What ifc_validate names, and what the session's edits touched (since 0.12.0), on synthetic models.

flo2-ifc's design 1510fc3728665dbf, fact:ifc-validate-names-an-issue-by-its-message-and-drops-the-element-and-
attribute-ifcopenshell-gives-it-2026-11-05: an issue said only its level and message, so a chat could not find the
element it was about and flo2's save could match errors only by counting messages. And flo2's design
1a97fc1f1de4d5d2, dec:a-save-checks-only-what-the-edits-touched-and-a-file-versions-own-check-is-kept: a save of a
large model checked the WHOLE model, three calls of 30 s, whatever the edit did. So:

- every issue names its element (step id, IFC class, GlobalId) and attribute, beside its level and message;
- the session records what each tool that changes the model created, changed and removed (``edits.Record``), and
  ``ifc_edited`` lists it;
- ``ifc_validate`` with ``scope: "edits"`` checks only those and their neighbours, and refuses by name when the
  record cannot say what the edits touched.

tests/test_edits_class.py holds the narrow check to finding every error the whole-model check would.
"""

from __future__ import annotations

import json

import anyio
import ifcopenshell
import ifcopenshell.api
import pytest

from flo2_ifc import building, edits, reading
from flo2_ifc.server import build_server
from tests import models


def _issues(v: dict) -> list[dict]:
    return [i for i in v["issues"] if i["level"] == "error"]


# ------------------------------------------------------------ 1. every issue names its element and attribute


def test_an_issue_keeps_its_level_and_message_and_names_its_element_and_attribute():
    f = models.building(walls=3)
    rel = f.by_type("IfcRelContainedInSpatialStructure")[0]
    rel.RelatingStructure = None
    v = reading.validate(f)
    [issue] = _issues(v)
    assert issue["level"] == "error" and issue["message"] == "Attribute not optional"
    assert issue["element"] == {"id": rel.id(), "type": "IfcRelContainedInSpatialStructure", "global_id": rel.GlobalId}
    assert issue["attribute"] == "IfcRelContainedInSpatialStructure.RelatingStructure"
    assert v["scope"] == "model"


def test_an_issue_on_an_instance_with_no_globalid_names_it_with_none():
    f = models.building(walls=1)
    placement = f.by_type("IfcLocalPlacement")[0]
    placement.RelativePlacement = None
    [issue] = _issues(reading.validate(f))
    assert issue["element"] == {"id": placement.id(), "type": "IfcLocalPlacement", "global_id": None}
    assert issue["attribute"] == "IfcLocalPlacement.RelativePlacement"


def test_an_element_that_lost_its_relation_is_named_on_its_inverse():
    f = models.with_materials(walls=2)
    rel = f.by_type("IfcRelAssociatesMaterial")[0]
    usage = rel.RelatingMaterial
    f.remove(rel)
    [issue] = _issues(reading.validate(f))
    assert issue["message"].startswith("With inverse:") and "AssociatedTo" in issue["message"]
    assert issue["element"]["id"] == usage.id() and issue["element"]["type"] == "IfcMaterialLayerSetUsage"
    assert issue["attribute"] == "IfcMaterialLayerSetUsage.AssociatedTo"


def test_a_value_of_the_wrong_kind_names_its_attribute_with_its_class():
    """IfcOpenShell names this attribute alone ("RelatedObjects"); every issue on an element says `<class>.<attribute>`."""
    f = models.building(walls=1)
    rel = next(r for r in f.by_type("IfcRelAggregates") if len(r.RelatedObjects) == 1)
    f.remove(rel.RelatedObjects[0])
    [issue] = [i for i in _issues(reading.validate(f)) if (i["element"] or {}).get("id") == rel.id()]
    assert issue["message"].startswith("With attribute:")
    assert issue["attribute"] == "IfcRelAggregates.RelatedObjects"


def test_a_globalid_used_twice_names_both_elements():
    f = models.building(walls=3)
    a, _, b = f.by_type("IfcWall")
    b.GlobalId = a.GlobalId
    [issue] = [i for i in reading.validate(f)["issues"] if "IfcRoot.UR1" in i["message"]]
    assert issue["element"] == {"id": b.id(), "type": "IfcWall", "global_id": a.GlobalId}
    assert issue["other_element"] == {"id": a.id(), "type": "IfcWall", "global_id": a.GlobalId}
    assert issue["attribute"] == "IfcWall.GlobalId"


def test_an_invalid_globalid_names_its_attribute_not_the_last_one_checked():
    f = models.building(walls=2)
    wall = f.by_type("IfcWall")[0]
    wall.GlobalId = "not a GlobalId"
    [issue] = [i for i in reading.validate(f)["issues"] if "base64" in i["message"]]
    assert issue["element"]["id"] == wall.id()
    assert issue["attribute"] == "IfcWall.GlobalId"


def test_a_rule_issue_names_its_element_too():
    f = models.building(walls=2)
    wall = f.by_type("IfcWall")[0]
    wall.PredefinedType = "USERDEFINED"
    wall.ObjectType = None
    v = reading.validate(f, express_rules=True)
    assert v["rules"]["checked"] is True
    [issue] = [i for i in v["issues"] if "CorrectPredefinedType" in (i["attribute"] or "")]
    assert issue["element"] == {"id": wall.id(), "type": "IfcWall", "global_id": wall.GlobalId}


# ------------------------------------------------------------ 2. the record of what the edits touched


def _step(record: edits.Record, model, change):
    return record.change(model, "ifc_edit", change)


def test_a_record_names_what_an_edit_created_changed_and_removed():
    f = models.building(walls=3)
    record = edits.Record()
    record.opened(f, "ifc_load", "house.ifc")
    w0, w1, w2 = f.by_type("IfcWall")
    _step(record, f, lambda: ifcopenshell.api.run("attribute.edit_attributes", f, product=w0, attributes={"Name": "Renamed"}))
    _step(record, f, lambda: ifcopenshell.api.run("root.remove_product", f, product=w1))
    made = _step(record, f, lambda: ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcColumn", name="New"))
    listed = record.listing(f, limit=1000)
    by_how = {}
    for e in listed["elements"]:
        by_how.setdefault(e["how"], set()).add(e["id"])
    assert w0.id() in by_how["changed"]
    assert w1.id() in by_how["removed"]
    assert made.id() in by_how["created"]
    removed = next(e for e in listed["elements"] if e["id"] == w1.id())
    assert removed["type"] == "IfcWall" and removed["global_id"]
    assert w2.id() not in {e["id"] for e in listed["elements"]}
    assert listed["since"] == {"opened": "house.ifc", "by": "ifc_load"}


def test_a_removed_element_is_held_with_what_referenced_it_and_what_it_referenced():
    f = models.building(walls=2)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    wall = f.by_type("IfcWall")[0]
    step_id = wall.id()
    holders = {e.id() for e in f.get_inverse(wall)}
    refs = {e.id() for e in f.traverse(wall, max_levels=1)} - {step_id}
    _step(record, f, lambda: f.remove(wall))
    checked = set(record.scope(f).ids)
    assert holders <= checked, "what referenced it is checked: it lost a reference"
    assert refs <= checked, "what it referenced is checked: it lost a referrer, so an inverse may fail"
    assert step_id not in checked, "the element itself no longer exists"
    assert record.scope(f).counts["removed"] == 1


def test_an_element_made_and_removed_by_the_edits_is_not_listed():
    f = models.building(walls=1)
    record = edits.Record()
    record.opened(f, "ifc_new", None)
    made = _step(record, f, lambda: ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcColumn"))
    step_id = made.id()
    _step(record, f, lambda: f.remove(made))
    assert step_id not in {e["id"] for e in record.listing(f)["elements"]}


def test_a_retyped_element_is_changed_not_removed():
    f = models.building(walls=2)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    wall = f.by_type("IfcWall")[0]
    step_id = wall.id()
    _step(record, f, lambda: ifcopenshell.api.run("root.reassign_class", f, product=wall, ifc_class="IfcColumn"))
    listed = {e["id"]: e for e in record.listing(f)["elements"]}
    assert listed[step_id]["how"] == "changed" and listed[step_id]["type"] == "IfcColumn"


def test_the_listing_pages_and_narrows_by_class_and_by_how():
    f = models.building(walls=30)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    for wall in f.by_type("IfcWall"):
        _step(record, f, lambda wall=wall: ifcopenshell.api.run("attribute.edit_attributes", f, product=wall, attributes={"Name": "x"}))
    first = record.listing(f, element_type="IfcWall", limit=10)
    assert first["page"]["total"] == 30 and first["page"]["listed"] == 10 and first["page"]["next_offset"] == 10
    assert {e["type"] for e in first["elements"]} == {"IfcWall"}
    assert first["counts"]["changed"] >= 30
    neighbours = record.listing(f, how="neighbour", limit=1000)
    assert neighbours["elements"] and {e["how"] for e in neighbours["elements"]} == {"neighbour"}
    checked = record.listing(f, how="checked", limit=1000)
    assert checked["page"]["total"] == len(record.scope(f).ids)
    with pytest.raises(ValueError, match="how"):
        record.listing(f, how="sideways")


def test_a_record_that_saw_a_change_it_could_not_follow_is_refused_by_name():
    f = models.building(walls=1)
    record = edits.Record()
    record.opened(f, "ifc_load", None)

    def opens_its_own_transaction():
        f.begin_transaction()
        f.createIfcCartesianPoint((0.0, 0.0, 0.0))
        f.end_transaction()
        return f.createIfcCartesianPoint((1.0, 0.0, 0.0))  # after it: no transaction was open to see this

    record.change(f, "ifc_edit", opens_its_own_transaction)
    with pytest.raises(edits.Unknown, match="scope \"model\""):
        record.scope(f)


def test_a_tool_that_reads_but_adds_to_the_model_loses_the_record():
    f = models.building(walls=1)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    record.read(f, "ifc_tree", lambda: f.createIfcCartesianPoint((0.0, 0.0, 0.0)))
    with pytest.raises(edits.Unknown, match="ifc_tree"):
        record.scope(f)


def test_a_model_the_record_did_not_see_opened_is_refused():
    f, g = models.building(walls=1), models.building(walls=1)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    with pytest.raises(edits.Unknown, match="opened"):
        record.scope(g)


def test_a_step_inside_a_recorded_edit_is_taken_back_whole_and_the_record_holds():
    f = models.building(walls=1)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    before = len(f.entity_names())

    def fails_part_way():
        step = edits.Step(f)
        f.createIfcCartesianPoint((0.0, 0.0, 0.0))
        step.take_back()
        raise RuntimeError("refused")

    with pytest.raises(RuntimeError):
        record.change(f, "ifc_add_wall", fails_part_way)
    assert len(f.entity_names()) == before
    assert record.scope(f) is not None  # still known: the step stayed inside the record's transaction


# ------------------------------------------------------------ 3. the narrow check


def test_scope_edits_checks_only_what_the_edits_touched_and_says_so():
    f = models.building(walls=200)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    wall = f.by_type("IfcWall")[5]
    _step(record, f, lambda: ifcopenshell.api.run("attribute.edit_attributes", f, product=wall, attributes={"Name": "x"}))
    v = reading.validate(f, edits=record.scope(f))
    assert v["scope"] == "edits" and v["complete"] is True and v["valid"] is True
    assert 0 < v["checked"]["of"] < 50, v["checked"]
    assert v["checked"]["of"] < len(f.entity_names()) / 20
    assert v["edits"]["changed"] >= 1 and v["edits"]["neighbours"] >= 1
    assert "GlobalId" in v["note"]


def test_scope_edits_finds_an_inverse_error_on_a_neighbour_the_edit_never_touched():
    f = models.with_materials(walls=50)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    rel = f.by_type("IfcRelAssociatesMaterial")[0]
    usage = rel.RelatingMaterial
    _step(record, f, lambda: f.remove(rel))
    [issue] = _issues(reading.validate(f, edits=record.scope(f)))
    assert issue["element"]["id"] == usage.id()


def test_scope_edits_names_a_duplicate_globalid_anywhere_in_the_model():
    f = models.building(walls=20)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    a, b = f.by_type("IfcWall")[0], f.by_type("IfcWall")[-1]
    b.GlobalId = a.GlobalId  # outside the record: the whole model's GlobalIds are checked every call
    v = reading.validate(f, edits=record.scope(f))
    assert v["checked"]["of"] == 0 and any("IfcRoot.UR1" in i["message"] for i in v["issues"])


def test_scope_edits_keeps_to_its_time_and_goes_on_from_next_start():
    f = models.building(walls=100)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    for wall in f.by_type("IfcWall"):
        _step(record, f, lambda wall=wall: ifcopenshell.api.run("attribute.edit_attributes", f, product=wall, attributes={"Name": "x"}))
    scope = record.scope(f)
    v = reading.validate(f, edits=scope, seconds=0)
    assert v["complete"] is False and v["next_start"] == 1
    calls = 1
    while v["next_start"] is not None:
        v = reading.validate(f, edits=scope, start=v["next_start"], seconds=0.01)
        calls += 1
        assert calls < 10_000
    assert v["complete"] is True and v["checked"]["to"] == len(scope.ids)


def test_scope_edits_does_not_run_the_rules():
    f = models.building(walls=1)
    record = edits.Record()
    record.opened(f, "ifc_load", None)
    with pytest.raises(ValueError, match="express_rules"):
        reading.validate(f, express_rules=True, edits=record.scope(f))


def test_a_building_step_alone_still_runs_in_its_own_transaction():
    """Outside a recorded call (a test, a script) a step keeps its own transaction: a refused one changes nothing."""
    f = models.building(walls=1)
    before = len(f.entity_names())
    with pytest.raises(ValueError):
        building.add_wall(f, "GROUND", [0, 0], [0, 0], 3000, 200)
    assert len(f.entity_names()) == before


# ------------------------------------------------------------ 4. through the server


def _call(server, tool, arguments):
    from mcp.server.mcpserver.exceptions import ToolError

    async def run():
        try:
            result = await server.call_tool(tool, arguments)
        except ToolError as e:
            return {"refused": str(e)}
        text = "".join(b.text for b in result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    return anyio.run(run)


@pytest.fixture()
def served(tmp_path):
    path = tmp_path / "house.ifc"
    models.building(walls=40).write(str(path))
    server = build_server()
    _call(server, "ifc_load", {"path": str(path)})
    return server, path


def test_every_tool_is_classed_as_changing_replacing_or_reading_the_model(served):
    server, _ = served

    async def names():
        return [t.name for t in await server.list_tools()]

    served_names = set(anyio.run(names))
    classed = set(edits.CHANGES) | set(edits.REPLACES) | set(edits.READS)
    assert served_names == classed, f"not classed: {sorted(served_names - classed)}; classed but not served: {sorted(classed - served_names)}"
    assert not set(edits.CHANGES) & set(edits.READS)


def test_every_tool_that_changes_the_model_is_recorded(served):
    server, _ = served
    ground = _call(server, "ifc_select", {"selector": "IfcBuildingStorey"})["elements"][0]["id"]
    wall = _call(server, "ifc_select", {"selector": "IfcWall"})["elements"][0]["id"]
    made = {}
    made["ifc_edit"] = _call(server, "ifc_edit", {"function_path": "attribute.edit_attributes", "params": json.dumps({"product": wall, "attributes": json.dumps({"Name": "Renamed"})})})
    made["ifc_shape"] = _call(server, "ifc_shape", {"method": "polyline", "params": json.dumps({"points": [[0.0, 0.0], [1.0, 0.0]]})})
    made["ifc_add_wall"] = _call(server, "ifc_add_wall", {"storey": str(ground), "start": [0, 0], "end": [3000, 0], "height": 3000, "thickness": 200})
    made["ifc_add_slab"] = _call(server, "ifc_add_slab", {"storey": str(ground), "footprint": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]], "thickness": 200})
    made["ifc_add_roof"] = _call(server, "ifc_add_roof", {"storey": str(ground), "footprint": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]], "kind": "shed", "eave_height": 3000, "slope": 0.25, "thickness": 200})
    made["ifc_add_space"] = _call(server, "ifc_add_space", {"storey": str(ground), "footprint": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]], "height": 2700})
    made["ifc_add_unit"] = _call(server, "ifc_add_unit", {"unit_type": "THERMODYNAMICTEMPERATUREUNIT", "unit": "KELVIN"})
    made["ifc_quantify"] = _call(server, "ifc_quantify", {"rule": "IFC4QtoBaseQuantities", "selector": "IfcSpace"})
    for tool, out in made.items():
        assert "refused" not in out and out.get("ok") is not False, (tool, out)
    listed = _call(server, "ifc_edited", {"limit": 1000})
    ids = {e["id"]: e["how"] for e in listed["elements"]}
    assert ids[wall] == "changed"
    assert ids[made["ifc_shape"]["result"]["id"]] == "created", "ifc_shape"
    for tool in ("ifc_add_wall", "ifc_add_slab", "ifc_add_roof", "ifc_add_space"):
        assert ids[made[tool]["made"]["id"]] == "created", tool
    assert any(e["type"] == "IfcSIUnit" and e["how"] == "created" for e in listed["elements"]), "ifc_add_unit"
    assert any(e["type"] == "IfcElementQuantity" and e["how"] == "created" for e in listed["elements"]), "ifc_quantify"
    v = _call(server, "ifc_validate", {"scope": "edits"})
    assert v["scope"] == "edits" and v["complete"] is True, v
    whole = _call(server, "ifc_validate", {})
    assert whole["scope"] == "model" and whole["checked"]["of"] > v["checked"]["of"]


def test_a_refused_edit_inside_the_record_changes_nothing_and_the_record_holds(served):
    server, _ = served
    window = _call(server, "ifc_edit", {"function_path": "root.create_entity", "params": json.dumps({"ifc_class": "IfcWindow"})})
    pset = _call(server, "ifc_edit", {"function_path": "pset.add_pset", "params": json.dumps({"product": window["result"]["id"], "name": "Pset_WindowCommon"})})
    before = _call(server, "ifc_summary", {})["total_entities"]
    out = _call(server, "ifc_edit", {"function_path": "pset.edit_pset", "params": json.dumps({"pset": pset["result"]["id"], "properties": json.dumps({"ThermalTransmittance": 1.7})})})
    assert out["ok"] is False and "Not written" in out["error"]
    assert _call(server, "ifc_summary", {})["total_entities"] == before, "the refused write was taken back"
    v = _call(server, "ifc_validate", {"scope": "edits"})
    assert v["scope"] == "edits" and "refused" not in v, v
    assert {e["id"] for e in _call(server, "ifc_edited", {})["elements"]} >= {window["result"]["id"], pset["result"]["id"]}


def test_scope_edits_starts_again_when_a_model_is_opened_and_is_refused_with_none(served):
    server, path = served
    wall = _call(server, "ifc_select", {"selector": "IfcWall"})["elements"][0]["id"]
    _call(server, "ifc_edit", {"function_path": "attribute.edit_attributes", "params": json.dumps({"product": wall, "attributes": json.dumps({"Name": "Renamed"})})})
    assert _call(server, "ifc_edited", {})["counts"]["changed"] >= 1
    _call(server, "ifc_load", {"path": str(path)})
    again = _call(server, "ifc_edited", {})
    assert again["counts"] == {"created": 0, "changed": 0, "removed": 0, "neighbours": 0}
    assert again["since"]["opened"] == str(path) and again["since"]["by"] == "ifc_load"
    assert _call(server, "ifc_validate", {"scope": "edits"})["checked"]["of"] == 0
    _call(server, "ifc_reset", {})
    refused = _call(server, "ifc_validate", {"scope": "edits"})
    assert "refused" in refused


def test_scope_is_named_plainly_and_a_wrong_one_is_refused(served):
    server, _ = served
    out = _call(server, "ifc_validate", {"scope": "everything"})
    assert "refused" in out and "edits" in out["refused"] and "model" in out["refused"]

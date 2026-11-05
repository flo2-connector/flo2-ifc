"""THE CLASS TEST: a check of only what the edits touched finds every error the whole-model check would (since 0.12.0).

flo2's design 1a97fc1f1de4d5d2, dec:a-save-checks-only-what-the-edits-touched-and-a-file-versions-own-check-is-kept,
names the risk this holds: an element can come to fail without being edited (an inverse rule fails on an element whose
relation was removed; a required attribute is emptied when what it named is deleted), so a check of the touched
elements alone would miss it. ``ifc_validate`` with ``scope: "edits"`` checks the touched elements AND their
neighbours (what each references and what references it, before and after the edits) and the whole model's GlobalIds.

Each case below makes real edits, recorded as the server records a tool's call (``edits.Record.change``), and then
compares the two checks ELEMENT BY ELEMENT, an issue being its element, attribute, level and message:

- the narrow check finds nothing the whole check does not;
- every error the edits added (the whole check of the edited model, less the whole check of the model as opened) is
  found by the narrow check.

The edits: walls removed (``root.remove_product``); a material association removed and nothing else (the element it
named is left without one, as an exporter or a partial edit leaves it); a required attribute emptied
(``attribute.edit_attributes``); a room added (flo2-ifc's ``add_space``); an element moved
(``geometry.edit_object_placement``) and given another class (``root.reassign_class``); a storey and a material
deleted while others still name them; and all of them at once.

They run on synthetic models (tests/models.py) always, and on openly licensed public sample models when
``FLO2_IFC_SAMPLES`` names a folder that holds them (README.md, "The tests and the checks"): flo2-ifc's
req:flo2-ifc-measures-whole-buildings-on-openly-licensed-sample-models. A sample is read with IfcOpenShell only.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.placement
import numpy as np
import pytest

from flo2_ifc import building, edits, reading
from tests import models

#: The public samples the class test runs on when FLO2_IFC_SAMPLES names their folder (README.md names each and its
#: source): KIT's AC20-FZK-Haus (IFC4) and buildingSMART's Duplex (IFC2X3).
SAMPLES = ("AC20-FZK-Haus.ifc", "Duplex_A_20110907.ifc")
SAMPLES_DIR = os.environ.get("FLO2_IFC_SAMPLES", "")
#: As long as a whole check of a sample takes on a laptop, in one call.
WHOLE = 600.0


def key(issue: dict[str, Any]) -> tuple:
    element = issue.get("element") or {}
    return (element.get("id"), issue.get("attribute"), issue["level"], issue["message"])


def whole(model: ifcopenshell.file) -> Counter:
    v = reading.validate(model, seconds=WHOLE)
    assert v["complete"], "the whole check did not finish in one call"
    assert "issues_withheld" not in v, "more issues than one reply names: the comparison would be partial"
    return Counter(key(i) for i in v["issues"])


def narrow(model: ifcopenshell.file, record: edits.Record) -> tuple[Counter, dict[str, Any]]:
    v = reading.validate(model, seconds=WHOLE, edits=record.scope(model))
    assert v["complete"] and v["scope"] == "edits"
    assert "issues_withheld" not in v
    return Counter(key(i) for i in v["issues"]), v


# ------------------------------------------------------------ the edits, each of whatever the model holds


def _remove_walls(m):
    walls = m.by_type("IfcWall")[:2] or m.by_type("IfcFlowSegment")[:2]
    for w in walls:
        ifcopenshell.api.run("root.remove_product", m, product=w)
    return len(walls)


def _remove_a_material_association(m):
    rels = m.by_type("IfcRelAssociatesMaterial")
    rel = next((r for r in rels if r.RelatingMaterial.is_a("IfcMaterialLayerSetUsage")), rels[0] if rels else None)
    if rel is None:
        return 0
    m.remove(rel)
    return 1


def _empty_a_required_attribute(m):
    rel = m.by_type("IfcRelContainedInSpatialStructure")[0]
    ifcopenshell.api.run("attribute.edit_attributes", m, product=rel, attributes={"RelatingStructure": None})
    return 1


def _add_a_room(m):
    storey = m.by_type("IfcBuildingStorey")[0]
    side = 1.0 / reading.unit_scale(m)  # one metre in the model's unit
    building.add_space(m, storey.id(), [[0, 0], [3 * side, 0], [3 * side, 2 * side], [0, 2 * side]], 2.5 * side, name="Added room")
    return 1


def _move_and_retype_an_element(m):
    element = next(iter(m.by_type("IfcDoor") or m.by_type("IfcFlowTerminal") or m.by_type("IfcWall")), None)
    if element is None:
        return 0
    matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement) if element.ObjectPlacement else np.eye(4)
    matrix = np.array(matrix)
    matrix[0, 3] += 0.5 / reading.unit_scale(m)
    ifcopenshell.api.run("geometry.edit_object_placement", m, product=element, matrix=matrix, is_si=False)
    ifcopenshell.api.run("root.reassign_class", m, product=element, ifc_class="IfcBuildingElementProxy")
    return 1


def _delete_a_storey_others_name(m):
    storey = m.by_type("IfcBuildingStorey")[-1]
    m.remove(storey)
    return 1


def _delete_a_material_others_name(m):
    material = next((x for x in m.by_type("IfcMaterial") if m.get_inverse(x)), None)
    if material is None:
        return 0
    m.remove(material)
    return 1


#: In the order "all at once" makes them: what moves and adds first, as a chat's session would, before what leaves
#: the model broken (an element on a storey whose containment names no storey cannot be moved).
EDITS: dict[str, Callable[[ifcopenshell.file], int]] = {
    "add a room": _add_a_room,
    "move and retype an element": _move_and_retype_an_element,
    "remove walls": _remove_walls,
    "remove a material association": _remove_a_material_association,
    "empty a required attribute": _empty_a_required_attribute,
    "delete a storey others name": _delete_a_storey_others_name,
    "delete a material others name": _delete_a_material_others_name,
}


def apply(model: ifcopenshell.file, names: list[str]) -> tuple[edits.Record, int]:
    """The edits named, each as one recorded tool call, as the server records ifc_edit's."""
    record = edits.Record()
    record.opened(model, "ifc_load", None)
    made = 0
    for name in names:
        made += record.change(model, "ifc_edit", lambda name=name: EDITS[name](model))
    return record, made


def compare(original: Counter, model: ifcopenshell.file, record: edits.Record) -> dict[str, Any]:
    after = whole(model)
    found, v = narrow(model, record)
    added = after - original
    assert not (found - after), f"the narrow check found what the whole check does not: {sorted(found - after)[:5]}"
    missed = added - found
    assert not missed, f"the narrow check missed errors the edits added: {sorted(missed)[:5]}"
    return {"added": sum(added.values()), "checked": v["checked"]["of"], "edits": v["edits"], "of": len(model.entity_names())}


# ------------------------------------------------------------ on synthetic models


def _synthetic() -> ifcopenshell.file:
    return models.with_materials(walls=6)


@pytest.fixture(scope="module")
def synthetic_original() -> Counter:
    return whole(_synthetic())


@pytest.mark.parametrize("edit", list(EDITS))
def test_the_narrow_check_finds_every_error_an_edit_adds_on_a_synthetic_model(edit, synthetic_original):
    model = _synthetic()
    record, made = apply(model, [edit])
    assert made, edit
    compare(synthetic_original, model, record)


def test_the_edits_add_errors_the_touched_elements_alone_do_not_hold(synthetic_original):
    """Not vacuous: the edits add errors, and some are on elements no edit touched (only their neighbours were)."""
    model = _synthetic()
    record, _ = apply(model, list(EDITS))
    out = compare(synthetic_original, model, record)
    assert out["added"] >= 3, out
    touched = {e["id"] for e in record.listing(model, limit=1000)["elements"] if e["how"] in ("created", "changed")}
    on_neighbours = [k for k in (whole(model) - synthetic_original) if k[0] is not None and k[0] not in touched]
    assert on_neighbours, "some added error is on an element only a neighbour of the edits"
    assert out["checked"] < out["of"]


# ------------------------------------------------------------ on public sample models


def _samples() -> list[Path]:
    if not SAMPLES_DIR:
        return []
    return [Path(SAMPLES_DIR) / n for n in SAMPLES if (Path(SAMPLES_DIR) / n).is_file()]


@pytest.mark.skipif(not _samples(), reason="FLO2_IFC_SAMPLES does not name a folder holding the public samples")
@pytest.mark.parametrize("sample", _samples(), ids=lambda p: p.name)
@pytest.mark.parametrize("edit", [*EDITS, "all at once"])
def test_the_narrow_check_finds_every_error_the_edits_add_on_a_public_sample(sample, edit, request):
    cache = request.config.cache
    original = getattr(request.module, "_originals", {}).get(sample)
    if original is None:
        original = whole(ifcopenshell.open(str(sample)))
        request.module._originals = {**getattr(request.module, "_originals", {}), sample: original}
    model = ifcopenshell.open(str(sample))
    record, made = apply(model, list(EDITS) if edit == "all at once" else [edit])
    if not made:
        pytest.skip(f"{sample.name} holds nothing to {edit}")
    t = time.monotonic()
    out = compare(original, model, record)
    out["seconds_both"] = round(time.monotonic() - t, 1)
    if cache is not None:
        cache.set(f"flo2-ifc/edits-class/{sample.name}/{edit}", out)

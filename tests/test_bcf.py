"""Writing a BCF 3.0 file (src/flo2_ifc/bcf_writing.py) with IfcOpenShell's bcf-client, on a synthetic model.

What must hold:

- the file opens again with bcf-client and holds the project, every topic (its title, description, type, status,
  labels, author and comments), each topic's components selected (and coloured) in its viewpoint, its camera, its
  snapshot and the model files its header names (with the IfcProject of the model open);
- every part of it is valid against buildingSMART's BCF 3.0 schemas, which this package ships unchanged (each held
  to its sha256 here);
- a description that cannot be written is refused with the place it is wrong, and nothing is written: not JSON, a
  topic missing its title, a GlobalId that is not one, more topics or components than the limits, a snapshot that is
  not a picture.

No real model is a fixture here.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import ifcopenshell
import pytest

from flo2_ifc import bcf_writing as B
from tests import models

#: buildingSMART's BCF 3.0 schemas, as copied from BCF-XML tag v3.0 (schemas/bcf-3.0/README.md).
SCHEMA_SHA256 = {
    "documents.xsd": "105da6cd7452e6a7c9ea54a6f0ac2f0420e7d8c6fb8d2ac9f6f2075edae4178f",
    "extensions.xsd": "df9b49fc2368aba41cec8259fad266fe82d735b5319469834e7fe19bf0d83153",
    "markup.xsd": "6c80f1c566ace8b405ae0ab530438aec2014bf35dd14cfc3c4cf0c5aa0b9d169",
    "project.xsd": "44cf05c63fddcba335589cbbf5824458118561ffc23bb6c3e72a1f3217cce68a",
    "shared-types.xsd": "93d20dfcc6d7f451ae85a8277559b0347478c278218a42f0ef41dcc5550a2090",
    "version.xsd": "cef0dcc112239457776cab33a5adeed0cf61c344e05bb5c6c4674c2df3643fa6",
    "visinfo.xsd": "0855bc002310b4a9decd67b1b21631bd2e0368f0878183da8448e34d807e7418",
}
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)


@pytest.fixture()
def house(tmp_path):
    path = str(tmp_path / "house.ifc")
    models.building(walls=3).write(path)
    return ifcopenshell.open(path), path


def _walls(model):
    return [w.GlobalId for w in sorted(model.by_type("IfcWall"), key=lambda w: w.Name)]


def _description(model, tmp_path):
    snapshot = tmp_path / "house 3D iso.png"
    snapshot.write_bytes(PNG)
    w = _walls(model)
    return {
        "project": "Test house",
        "topics": [
            {
                "title": "The kitchen wall is renamed",
                "description": "Renamed so the schedule reads by room. The owner's reason: the kitchen moves here.",
                "topic_type": "Request",
                "topic_status": "Open",
                "author": "owner@example.com",
                "labels": ["architecture", "kitchen"],
                "components": [w[0]],
                "colour": "orange",
                "snapshot_path": str(snapshot),
                "files": [{"filename": "house.ifc", "date": "2026-11-02T10:00:00Z"}, "house-after.ifc"],
                "comments": [{"author": "owner@example.com", "comment": "So the plan reads by room.", "date": "2026-11-02T10:05:00Z"}],
            },
            {
                "title": "Two walls get a fire rating",
                "description": "EI60 on both, as the builder asked.",
                "topic_type": "Request",
                "topic_status": "Closed",
                "author": "owner@example.com",
                "components": w[1:],
                "colours": [{"colour": "FF0000", "components": [w[1]]}, {"colour": "00A000"}],
                "camera": {"position": [12.0, -8.0, 9.0], "direction": [-0.6, 0.6, -0.5], "up": [0, 0, 1], "field_of_view": 50},
            },
            {
                "title": "The front door is deleted",
                "description": "It is no longer wanted.",
                "topic_type": "Remark",
                "topic_status": "Open",
                "author": "owner@example.com",
                "components": ["0FcSBOkY17wQ_tyd0OAGdR"],
                "camera": {"kind": "orthogonal", "position": [0, 0, 30], "direction": [0, 0, -1], "up": [0, 1, 0], "view_to_world_scale": 20},
            },
        ],
    }


def test_the_schemas_are_buildingsmarts_unchanged():
    for name, digest in SCHEMA_SHA256.items():
        assert hashlib.sha256((B.SCHEMA_DIR / name).read_bytes()).hexdigest() == digest, name
    assert sorted(p.name for p in B.SCHEMA_DIR.glob("*.xsd")) == sorted(SCHEMA_SHA256)


def test_a_bcf_is_written_and_opens_again_with_bcf_client_holding_what_it_was_given(house, tmp_path):
    from bcf.v3.bcfxml import BcfXml

    model, path = house
    desc = _description(model, tmp_path)
    out = B.write_bcf(desc, str(tmp_path / "what changed"), model=model, model_path=path)
    assert out["valid"] is True and out["bcf_version"] == "3.0"
    assert out["written"] == str(tmp_path / "what changed.bcf")
    data = Path(out["written"]).read_bytes()
    assert out["sha256"] == hashlib.sha256(data).hexdigest() and out["bytes"] == len(data)
    assert [t["title"] for t in out["topics"]] == [t["title"] for t in desc["topics"]]

    back = BcfXml.load(Path(out["written"]))
    assert back.project_info.project.name == "Test house"
    assert back.version.version_id == "3.0"
    by_title = {th.topic.title: th for th in back.topics.values()}
    assert set(by_title) == {t["title"] for t in desc["topics"]}
    w = _walls(model)

    renamed = by_title["The kitchen wall is renamed"]
    assert renamed.topic.description.startswith("Renamed so the schedule reads by room")
    assert (renamed.topic.topic_type, renamed.topic.topic_status, renamed.topic.creation_author) == ("Request", "Open", "owner@example.com")
    assert renamed.topic.labels.label == ["architecture", "kitchen"]
    assert [(c.author, c.comment) for c in renamed.comments] == [("owner@example.com", "So the plan reads by room.")]
    (vp,) = renamed.viewpoints.values()
    assert vp.get_selected_guids() == [w[0]]
    colours = vp.visualization_info.components.coloring.color
    assert [(c.color, [x.ifc_guid for x in c.components.component]) for c in colours] == [("FF8000", [w[0]])]
    assert vp.snapshot == PNG, "the picture kept in the design is the viewpoint's snapshot"
    assert vp.visualization_info.perspective_camera is not None, "aimed at the wall in the open model"
    files = renamed.header.files.file
    assert [f.filename for f in files] == ["house.ifc", "house-after.ifc"]
    assert files[0].ifc_project == model.by_type("IfcProject")[0].GlobalId, "the open model's own project"
    assert files[1].ifc_project is None
    assert renamed.comments[0].viewpoint.guid == vp.guid

    rated = by_title["Two walls get a fire rating"]
    (vp,) = rated.viewpoints.values()
    assert sorted(vp.get_selected_guids()) == sorted(w[1:])
    colours = {c.color: [x.ifc_guid for x in c.components.component] for c in vp.visualization_info.components.coloring.color}
    assert colours == {"FF0000": [w[1]], "00A000": w[1:]}
    cam = vp.visualization_info.perspective_camera
    assert (cam.camera_view_point.x, cam.camera_view_point.y, cam.camera_view_point.z, cam.field_of_view) == (12.0, -8.0, 9.0, 50.0)

    deleted = by_title["The front door is deleted"]
    (vp,) = deleted.viewpoints.values()
    assert vp.visualization_info.orthogonal_camera.view_to_world_scale == 20.0
    back.close()
    said = {t["title"]: t for t in out["topics"]}
    assert said["The front door is deleted"]["camera"] == "given"
    assert said["The kitchen wall is renamed"]["camera"].startswith("aimed at its elements")
    assert said["The kitchen wall is renamed"]["snapshot"] is True


def test_every_part_is_valid_against_the_bcf_3_schemas(house, tmp_path):
    model, path = house
    out = B.write_bcf(_description(model, tmp_path), str(tmp_path / "rec"), model=model, model_path=path)
    assert B._check_parts(out["written"]) == []
    with zipfile.ZipFile(out["written"]) as z:
        names = z.namelist()
    assert {"bcf.version", "project.bcfp", "extensions.xml"} <= set(names)
    assert sum(n.endswith("/markup.bcf") for n in names) == 3 and sum(n.endswith(".bcfv") for n in names) == 3
    # A part that is not valid is caught: a markup with no title.
    bad = tmp_path / "bad.bcf"
    with zipfile.ZipFile(out["written"]) as z, zipfile.ZipFile(bad, "w") as w:
        for n in z.namelist():
            body = z.read(n)
            if n.endswith("/markup.bcf"):
                body = body.replace(b"<Title>", b"<Nonsense>").replace(b"</Title>", b"</Nonsense>")
            w.writestr(n, body)
    assert any("markup.bcf" in e for e in B._check_parts(str(bad)))


def test_the_extensions_list_every_type_status_label_and_user_used(house, tmp_path):
    from bcf.v3.bcfxml import BcfXml

    model, path = house
    out = B.write_bcf(_description(model, tmp_path), str(tmp_path / "rec"), model=model, model_path=path)
    ext = BcfXml.load(Path(out["written"])).extensions
    assert ext.topic_types.topic_type == ["Request", "Remark"]
    assert ext.topic_statuses.topic_status == ["Open", "Closed"]
    assert ext.topic_labels.topic_label == ["architecture", "kitchen"]
    assert ext.users.user == ["owner@example.com"]


def test_needs_no_model_and_names_elements_the_open_model_does_not_hold(house, tmp_path):
    model, path = house
    desc = _description(model, tmp_path)
    alone = B.write_bcf(desc, str(tmp_path / "alone"))
    assert alone["valid"] is True
    assert alone["topics"][0]["camera"].startswith("the model's origin")
    desc["topics"][0]["components"].append("0FcSBOkY17wQ_tyd0OAGdR")
    held = B.write_bcf(desc, str(tmp_path / "held"), model=model, model_path=path)
    assert held["topics"][0]["not_in_open_model"] == ["0FcSBOkY17wQ_tyd0OAGdR"]


# ---------------------------------------------------------------- refusals, each with nothing written


def _refused(tmp_path, desc, match, **kw):
    with pytest.raises(B.BcfRefused, match=match):
        B.write_bcf(desc, str(tmp_path / "refused"), **kw)
    assert not os.path.exists(tmp_path / "refused.bcf")


def _one(**topic):
    return {"project": "P", "topics": [{"title": "T", "description": "D", "topic_type": "Request", "topic_status": "Open", "author": "a@example.com", "components": ["2O2Fr$t4X7Zf8NOew3FNr2"], **topic}]}


def test_a_description_that_is_not_json_or_lacks_a_title_is_refused(tmp_path):
    _refused(tmp_path, "<Markup/>", "the description is not JSON")
    desc = _one()
    del desc["topics"][0]["title"]
    _refused(tmp_path, desc, r"topic 1: `title` is needed")
    _refused(tmp_path, {"project": "P", "topics": []}, "at least one topic")
    _refused(tmp_path, _one(guid="x"), "a topic takes .*not guid")


def test_a_component_that_is_not_a_globalid_is_refused(tmp_path):
    _refused(tmp_path, _one(components=["#131"]), r"'#131' is not an IFC GlobalId")
    _refused(tmp_path, _one(components=[]), "at least one")


def test_more_topics_or_components_than_the_limits_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "MAX_TOPICS", 2)
    desc = _one()
    desc["topics"] = desc["topics"] * 3
    _refused(tmp_path, desc, "3 topics; one file holds at most 2")
    monkeypatch.setattr(B, "MAX_COMPONENTS", 2)
    many = ["2O2Fr$t4X7Zf8NOew3FNr2", "2O2Fr$t4X7Zf8NOew3FNr3", "2O2Fr$t4X7Zf8NOew3FNr4"]
    _refused(tmp_path, _one(components=many), "3 components; one topic selects at most 2")
    monkeypatch.setattr(B, "MAX_COMMENTS", 1)
    _refused(tmp_path, _one(comments=["one", "two"]), "2 comments; a topic takes at most 1")


def test_the_defaults_are_the_documented_limits():
    assert (B.MAX_TOPICS, B.MAX_COMPONENTS, B.MAX_ALL_COMPONENTS, B.MAX_COMMENTS) == (100, 2000, 20000, 50)


def test_a_snapshot_that_is_not_a_picture_and_a_bad_camera_or_colour_are_refused(tmp_path):
    text = tmp_path / "notes.png"
    text.write_text("not a picture")
    _refused(tmp_path, _one(snapshot_path=str(text)), "is not a PNG or JPEG picture")
    _refused(tmp_path, _one(snapshot_path=str(tmp_path / "missing.png")), "there is no picture at")
    _refused(tmp_path, _one(camera={"position": [0, 0], "direction": [1, 0, 0]}), r"\[x, y, z\]")
    _refused(tmp_path, _one(camera={"kind": "orthogonal", "position": [0, 0, 9], "direction": [0, 0, -1]}), "view_to_world_scale")
    _refused(tmp_path, _one(colour="mauve"), "a colour is hex RGB")
    _refused(tmp_path, _one(colours=[{"colour": "FF0000", "components": ["2O2Fr$t4X7Zf8NOew3FNr9"]}]), "not one of the topic's components")

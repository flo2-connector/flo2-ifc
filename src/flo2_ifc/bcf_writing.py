"""Writing a BCF 3.0 file with IfcOpenShell's own BCF library (bcf-client), checked against buildingSMART's schemas.

BCF (buildingSMART's BIM Collaboration Format) is how building tools pass
issues and changes pinned to a model's elements: each TOPIC has a title, a
description, a type, a status, labels, an author and comments, and a
VIEWPOINT that selects the elements it is about by their IFC GlobalId (and may
colour them), seen from a camera, with a picture (the snapshot). Any BCF tool
(Bonsai, Solibri, BIMcollab, Revit's BCF add-ins) opens one.

``write_bcf`` builds the file from a description (the project's name and its
topics) with bcf-client's own model and writer, never XML by hand, then:

- checks every part of the file it wrote (bcf.version, project.bcfp,
  extensions.xml, each topic's markup.bcf and viewpoint) against
  buildingSMART's BCF 3.0 XML schemas, shipped in this package
  (``schemas/bcf-3.0``, unchanged, with their licence) and read with
  xmlschema, offline;
- opens it again with bcf-client and checks it holds the topics, components
  and comments it was given;

and refuses, with nothing kept, when either fails.

A viewpoint's camera is the one a topic gives, in METRES, as BCF has it; or,
when the model open in the session holds the topic's elements, bcf-client's
own camera aimed at them (``camera_vectors_from_target_position``, from above
one corner, far enough to take them all in); or, when it holds none of them,
the same camera aimed at the model's origin, and the reply says so. BCF 3.0
requires a camera on every viewpoint.

Every limit (``MAX_*``) is an environment setting, and the reply never carries
the file itself.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from flo2_ifc import geometry

#: Topics one BCF file may hold. A record of what changed is one topic per change
#: a person meant; a model's whole clash list belongs in a clash tool's own BCF.
MAX_TOPICS = int(os.environ.get("FLO2_IFC_BCF_MAX_TOPICS", "100"))
#: Elements one topic may select, and all its topics together.
MAX_COMPONENTS = int(os.environ.get("FLO2_IFC_BCF_MAX_COMPONENTS", "2000"))
MAX_ALL_COMPONENTS = int(os.environ.get("FLO2_IFC_BCF_MAX_ALL_COMPONENTS", "20000"))
#: Comments, labels and colours one topic may carry.
MAX_COMMENTS = int(os.environ.get("FLO2_IFC_BCF_MAX_COMMENTS", "50"))
MAX_LABELS = int(os.environ.get("FLO2_IFC_BCF_MAX_LABELS", "20"))
MAX_COLOURS = 16
#: Characters a title, a short value (a type, a status, a label, an author) and a text (a description, a comment) may be.
MAX_TITLE = 256
MAX_SHORT = 128
MAX_TEXT = int(os.environ.get("FLO2_IFC_BCF_MAX_TEXT", "20000"))
#: Files a topic's header may name.
MAX_FILES = 10
#: The largest snapshot picture one viewpoint takes (a render_model PNG is 1280 x 960, about 20 to 300 KB).
MAX_SNAPSHOT_BYTES = int(os.environ.get("FLO2_IFC_BCF_MAX_SNAPSHOT_BYTES", str(8 * 1024 * 1024)))
#: How far the camera bcf-client aims at a topic's elements stands back at least, in metres along each axis (its own
#: default, 5 m).
CAMERA_STANDOFF = 5.0

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "bcf-3.0"
#: Which schema checks which part of a BCF file.
PART_SCHEMAS = {"bcf.version": "version", "project.bcfp": "project", "extensions.xml": "extensions", "documents.xml": "documents"}
#: An IFC GlobalId: 22 characters of IFC's base-64 alphabet, the first 0 to 3 (128 bits).
GLOBAL_ID = re.compile(r"^[0-3][0-9A-Za-z_$]{21}$")
COLOUR = re.compile(r"^#?([0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?)$")
NAMED_COLOURS = {"red": "FF0000", "orange": "FF8000", "yellow": "FFD700", "green": "00A000", "blue": "0060FF", "purple": "8000C0", "grey": "808080", "gray": "808080"}
TOPIC_KEYS = {"title", "description", "topic_type", "topic_status", "author", "labels", "priority", "stage", "assigned_to", "due_date", "date",
              "components", "colour", "colours", "camera", "snapshot_path", "files", "comments"}
CAMERA_KEYS = {"kind", "position", "direction", "up", "field_of_view", "view_to_world_scale", "aspect_ratio"}


class BcfRefused(ValueError):
    """A BCF description that cannot be written, with the reason a chat can correct."""


@lru_cache(maxsize=None)
def schema(name: str):
    """buildingSMART's BCF 3.0 schema `name` (markup, visinfo, ...), read from this package with no network."""
    import xmlschema

    return xmlschema.XMLSchema(str(SCHEMA_DIR / f"{name}.xsd"))


def _bcf_version() -> str:
    try:
        from importlib.metadata import version

        return version("bcf-client")
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------- reading the description


def _text(raw: dict[str, Any], key: str, where: str, most: int, required: bool = False) -> str | None:
    v = raw.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise BcfRefused(f"{where}: `{key}` is needed")
        return None
    if not isinstance(v, str):
        raise BcfRefused(f"{where}: `{key}` is text")
    v = v.strip()
    if len(v) > most:
        raise BcfRefused(f"{where}: `{key}` is {len(v)} characters; at most {most}")
    return v


def _date(raw: Any, where: str) -> _dt.datetime:
    if raw is None or raw == "":
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    if not isinstance(raw, str):
        raise BcfRefused(f"{where}: a date is ISO 8601 text, e.g. \"2026-11-02T14:30:00Z\"")
    try:
        d = _dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise BcfRefused(f"{where}: {raw!r} is not an ISO 8601 date, e.g. \"2026-11-02\" or \"2026-11-02T14:30:00Z\"") from e
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def _xml_date(d: _dt.datetime):
    from xsdata.models.datatype import XmlDateTime

    return XmlDateTime.from_datetime(d)


def _guids(raw: Any, where: str) -> list[str]:
    if isinstance(raw, str):
        raw = [g for g in re.split(r"[\s,]+", raw) if g]
    if not isinstance(raw, list) or not all(isinstance(g, str) for g in raw):
        raise BcfRefused(f"{where}: elements are a list of IFC GlobalIds, e.g. [\"2O2Fr$t4X7Zf8NOew3FNr2\"]")
    out: list[str] = []
    for g in raw:
        g = g.strip()
        if not GLOBAL_ID.match(g):
            raise BcfRefused(f"{where}: {g!r} is not an IFC GlobalId (22 characters of 0-9, A-Z, a-z, _ and $, as ifc_diff, ifc_select and ifc_info give them)")
        if g not in out:
            out.append(g)
    return out


def _colour(raw: Any, where: str) -> str:
    if isinstance(raw, str) and raw.strip().lower() in NAMED_COLOURS:
        return NAMED_COLOURS[raw.strip().lower()]
    m = COLOUR.match(raw.strip()) if isinstance(raw, str) else None
    if not m:
        raise BcfRefused(f"{where}: a colour is hex RGB like \"FF8000\" (or ARGB \"FF8000FF\"), or one of {', '.join(sorted(set(NAMED_COLOURS) - {'gray'}))}")
    return m.group(1).upper()


def _vector(raw: Any, where: str) -> list[float]:
    if not (isinstance(raw, list) and len(raw) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in raw)):
        raise BcfRefused(f"{where}: a point or direction is [x, y, z], three numbers in metres")
    return [float(x) for x in raw]


def _camera(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BcfRefused(f"{where}: `camera` is {{\"position\": [x, y, z], \"direction\": [x, y, z], \"up\": [x, y, z]}} in metres, optionally \"kind\" perspective (with \"field_of_view\" in degrees) or orthogonal (with \"view_to_world_scale\")")
    unknown = sorted(set(raw) - CAMERA_KEYS)
    if unknown:
        raise BcfRefused(f"{where}: `camera` takes {', '.join(sorted(CAMERA_KEYS))}, not {', '.join(unknown)}")
    kind = str(raw.get("kind", "perspective")).strip().lower()
    if kind not in ("perspective", "orthogonal"):
        raise BcfRefused(f"{where}: the camera's `kind` is perspective or orthogonal, not {raw.get('kind')!r}")
    cam = {"kind": kind, "position": _vector(raw.get("position"), f"{where} camera position"), "direction": _vector(raw.get("direction"), f"{where} camera direction"),
           "up": _vector(raw.get("up", [0, 0, 1]), f"{where} camera up")}
    if all(abs(x) < 1e-12 for x in cam["direction"]):
        raise BcfRefused(f"{where}: the camera's `direction` is not a direction (all zero)")
    ratio = raw.get("aspect_ratio", 1280 / 960)
    if not isinstance(ratio, (int, float)) or ratio <= 0:
        raise BcfRefused(f"{where}: the camera's `aspect_ratio` is a number more than 0 (width over height)")
    cam["aspect_ratio"] = float(ratio)
    if kind == "perspective":
        fov = raw.get("field_of_view", 60)
        if not isinstance(fov, (int, float)) or not 0 < fov < 180:
            raise BcfRefused(f"{where}: the camera's `field_of_view` is in degrees, more than 0 and less than 180")
        cam["field_of_view"] = float(fov)
    else:
        scale = raw.get("view_to_world_scale")
        if not isinstance(scale, (int, float)) or scale <= 0:
            raise BcfRefused(f"{where}: an orthogonal camera needs `view_to_world_scale`, the height of the view in metres, more than 0")
        cam["view_to_world_scale"] = float(scale)
    return cam


def _file_entries(raw: Any, where: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BcfRefused(f"{where}: `files` is a list of the model files the topic is about")
    if len(raw) > MAX_FILES:
        raise BcfRefused(f"{where}: `files` names {len(raw)} files; a topic names at most {MAX_FILES}")
    out = []
    for i, f in enumerate(raw):
        f = {"filename": f} if isinstance(f, str) else f
        if not isinstance(f, dict) or not isinstance(f.get("filename"), str) or not f["filename"].strip():
            raise BcfRefused(f"{where}, file {i + 1}: a file is its name, or {{\"filename\": ..., \"date\": ..., \"reference\": ...}}")
        unknown = sorted(set(f) - {"filename", "date", "reference", "ifc_project", "ifc_spatial_structure_element"})
        if unknown:
            raise BcfRefused(f"{where}, file {i + 1}: a file takes filename, date, reference, ifc_project and ifc_spatial_structure_element, not {', '.join(unknown)}")
        for k in ("ifc_project", "ifc_spatial_structure_element"):
            if f.get(k) is not None:
                _guids([f[k]], f"{where}, file {i + 1} {k}")
        out.append(f)
    return out


def _snapshot(path: Any, where: str) -> tuple[bytes, str]:
    if not isinstance(path, str) or not path.strip():
        raise BcfRefused(f"{where}: `snapshot_path` is the picture's path")
    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise BcfRefused(f"{where}: there is no picture at {path!r}") from e
    if size > MAX_SNAPSHOT_BYTES:
        raise BcfRefused(f"{where}: the picture is {size} bytes; a snapshot is at most {MAX_SNAPSHOT_BYTES}")
    with open(path, "rb") as f:
        data = f.read(MAX_SNAPSHOT_BYTES + 1)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "snapshot.png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, "snapshot.jpg"
    raise BcfRefused(f"{where}: {os.path.basename(path)!r} is not a PNG or JPEG picture, which is all a BCF snapshot can be")


def read_description(description: dict[str, Any] | str) -> dict[str, Any]:
    """The description, checked: BcfRefused with the place it is wrong."""
    if isinstance(description, str):
        try:
            description = json.loads(description)
        except json.JSONDecodeError as e:
            raise BcfRefused(f"the description is not JSON: {e}") from e
    if not isinstance(description, dict):
        raise BcfRefused("the description is an object: {\"project\": ..., \"topics\": [...]}")
    unknown = sorted(set(description) - {"project", "topics"})
    if unknown:
        raise BcfRefused(f"the description takes project and topics, not {', '.join(unknown)}")
    project = _text(description, "project", "the BCF", MAX_TITLE, required=True)
    topics = description.get("topics")
    if not isinstance(topics, list) or not topics:
        raise BcfRefused("the BCF: `topics` is a list of at least one topic")
    if len(topics) > MAX_TOPICS:
        raise BcfRefused(f"the BCF has {len(topics)} topics; one file holds at most {MAX_TOPICS} (one topic per change the person meant; write the rest to another file)")
    out = []
    total = 0
    for i, t in enumerate(topics):
        where = f"topic {i + 1}"
        if not isinstance(t, dict):
            raise BcfRefused(f"{where}: a topic is an object with title, description, topic_type, topic_status, author and components")
        unknown = sorted(set(t) - TOPIC_KEYS)
        if unknown:
            raise BcfRefused(f"{where}: a topic takes {', '.join(sorted(TOPIC_KEYS))}, not {', '.join(unknown)}")
        topic: dict[str, Any] = {
            "title": _text(t, "title", where, MAX_TITLE, required=True),
            "description": _text(t, "description", where, MAX_TEXT, required=True),
            "topic_type": _text(t, "topic_type", where, MAX_SHORT, required=True),
            "topic_status": _text(t, "topic_status", where, MAX_SHORT, required=True),
            "author": _text(t, "author", where, MAX_SHORT, required=True),
            "priority": _text(t, "priority", where, MAX_SHORT),
            "stage": _text(t, "stage", where, MAX_SHORT),
            "assigned_to": _text(t, "assigned_to", where, MAX_SHORT),
            "date": _date(t.get("date"), f"{where} date"),
            "due_date": _date(t["due_date"], f"{where} due_date") if t.get("due_date") else None,
        }
        labels = t.get("labels") or []
        if isinstance(labels, str):
            labels = [labels]
        if not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels):
            raise BcfRefused(f"{where}: `labels` is a list of words, e.g. [\"architecture\", \"kitchen\"]")
        if len(labels) > MAX_LABELS:
            raise BcfRefused(f"{where}: {len(labels)} labels; a topic takes at most {MAX_LABELS}")
        for x in labels:
            if len(x.strip()) > MAX_SHORT:
                raise BcfRefused(f"{where}: a label is at most {MAX_SHORT} characters")
        topic["labels"] = list(dict.fromkeys(x.strip() for x in labels))
        components = _guids(t.get("components") if t.get("components") is not None else [], f"{where} components")
        if not components:
            raise BcfRefused(f"{where}: `components` is the GlobalIds of the elements it is about (at least one), as ifc_diff gives them")
        if len(components) > MAX_COMPONENTS:
            raise BcfRefused(f"{where}: {len(components)} components; one topic selects at most {MAX_COMPONENTS} (split the change into topics, or name the elements it is really about)")
        total += len(components)
        if total > MAX_ALL_COMPONENTS:
            raise BcfRefused(f"the BCF's topics select more than {MAX_ALL_COMPONENTS} elements together; write fewer, or split them into two files")
        topic["components"] = components
        colours: list[tuple[str, list[str]]] = []
        if t.get("colour") is not None and t.get("colours") is not None:
            raise BcfRefused(f"{where}: give `colour` (every component in one colour) or `colours` (a colour for each set), not both")
        if t.get("colour") is not None:
            colours.append((_colour(t["colour"], f"{where} colour"), components))
        if t.get("colours") is not None:
            if not isinstance(t["colours"], list) or len(t["colours"]) > MAX_COLOURS:
                raise BcfRefused(f"{where}: `colours` is a list of at most {MAX_COLOURS} {{\"colour\": \"FF0000\", \"components\": [...]}}")
            for j, c in enumerate(t["colours"]):
                if not isinstance(c, dict) or set(c) - {"colour", "components"}:
                    raise BcfRefused(f"{where} colours {j + 1}: each is {{\"colour\": \"FF0000\", \"components\": [GlobalIds]}}")
                ids = _guids(c.get("components"), f"{where} colours {j + 1}") if c.get("components") is not None else components
                stray = [g for g in ids if g not in components]
                if stray:
                    raise BcfRefused(f"{where} colours {j + 1}: {stray[0]!r} is coloured but not one of the topic's components; add it to `components`")
                if not ids:
                    raise BcfRefused(f"{where} colours {j + 1}: a colour needs at least one element (BCF 3.0)")
                colours.append((_colour(c.get("colour"), f"{where} colours {j + 1}"), ids))
        topic["colours"] = colours
        topic["camera"] = _camera(t["camera"], where) if t.get("camera") is not None else None
        topic["snapshot"] = _snapshot(t["snapshot_path"], where) if t.get("snapshot_path") else None
        topic["files"] = _file_entries(t.get("files"), where)
        comments = t.get("comments") or []
        if not isinstance(comments, list):
            raise BcfRefused(f"{where}: `comments` is a list of {{\"author\": ..., \"comment\": ..., \"date\": optional}}")
        if len(comments) > MAX_COMMENTS:
            raise BcfRefused(f"{where}: {len(comments)} comments; a topic takes at most {MAX_COMMENTS}")
        said = []
        for j, c in enumerate(comments):
            w = f"{where} comment {j + 1}"
            if isinstance(c, str):
                c = {"comment": c, "author": topic["author"]}
            if not isinstance(c, dict) or set(c) - {"author", "comment", "date"}:
                raise BcfRefused(f"{w}: a comment is {{\"author\": ..., \"comment\": ..., \"date\": optional}}")
            said.append({"author": _text(c, "author", w, MAX_SHORT) or topic["author"], "comment": _text(c, "comment", w, MAX_TEXT, required=True), "date": _date(c.get("date"), f"{w} date")})
        topic["comments"] = said
        out.append(topic)
    return {"project": project, "topics": out}


# ---------------------------------------------------------------- the camera


def _aimed_camera(model, guids: list[str]) -> tuple[list[list[float]] | None, list[str], list[str]]:
    """bcf-client's camera aimed at the elements of `guids` the open model holds: (its vectors, or None when it holds
    none of them; the GlobalIds found; those not found)."""
    import numpy as np
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    from bcf.geometry import camera_vectors_from_target_position

    if model is None:
        return None, [], list(guids)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    found, missing, points = [], [], []
    for g in guids:
        try:
            e = model.by_guid(g)
        except RuntimeError:
            missing.append(g)
            continue
        found.append(g)
        placement = getattr(e, "ObjectPlacement", None)
        if placement is not None:
            try:
                points.append(ifcopenshell.util.placement.get_local_placement(placement)[:3, 3] * scale)
            except Exception:  # noqa: BLE001 - a placement IfcOpenShell cannot read is left out of the aim
                pass
    if not points:
        return None, found, missing
    pts = np.array(points)
    target = (pts.min(axis=0) + pts.max(axis=0)) / 2
    reach = float(np.max(pts.max(axis=0) - pts.min(axis=0))) if len(pts) > 1 else 0.0
    d = max(CAMERA_STANDOFF, reach)
    return [list(v) for v in camera_vectors_from_target_position(target, np.array((d, d, d)))], found, missing


def _camera_model(cam: dict[str, Any]):
    from bcf.v3 import model as mdl

    point = lambda v: mdl.Point(x=v[0], y=v[1], z=v[2])  # noqa: E731
    direction = lambda v: mdl.Direction(x=v[0], y=v[1], z=v[2])  # noqa: E731
    common = {"camera_view_point": point(cam["position"]), "camera_direction": direction(cam["direction"]), "camera_up_vector": direction(cam["up"]), "aspect_ratio": cam["aspect_ratio"]}
    if cam["kind"] == "orthogonal":
        return {"orthogonal_camera": mdl.OrthogonalCamera(view_to_world_scale=cam["view_to_world_scale"], **common)}
    return {"perspective_camera": mdl.PerspectiveCamera(field_of_view=cam["field_of_view"], **common)}


# ---------------------------------------------------------------- writing and checking


def _check_parts(path: str) -> list[str]:
    """Every part of the BCF file at `path` checked against its BCF 3.0 schema: the reasons it is not valid."""
    errors: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for required in ("bcf.version",):
            if required not in names:
                errors.append(f"it has no {required}")
        for n in names:
            if n.endswith("/"):
                continue
            kind = PART_SCHEMAS.get(n) or ("markup" if n.endswith("/markup.bcf") else "visinfo" if n.endswith(".bcfv") else None)
            if kind is None:
                continue
            for e in schema(kind).iter_errors(io.BytesIO(z.read(n))):
                errors.append(f"{n}: {' '.join(str(getattr(e, 'reason', e)).split())[:300]}" + (f" (at {e.path})" if getattr(e, "path", None) else ""))
    return errors


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def write_bcf(description: dict[str, Any] | str, output_path: str, model=None, model_path: str | None = None) -> dict[str, Any]:
    """Build a BCF 3.0 file from `description` with bcf-client, check it against the BCF 3.0 schemas and read it back,
    and write it as `output_path`.bcf. Refused, with nothing written, if any of that fails. `model`, the model open in
    the session if any, aims each topic's camera at its elements and fills in its IfcProject."""
    import contextlib

    from bcf.v3 import model as mdl
    from bcf.v3.bcfxml import BcfXml
    from bcf.v3.visinfo import VisualizationInfoHandler, build_components

    d = read_description(description)
    open_file = os.path.basename(model_path) if model_path else None
    project_guid = None
    if model is not None:
        try:
            project_guid = model.by_type("IfcProject")[0].GlobalId
        except (IndexError, RuntimeError):
            project_guid = None

    bcf = BcfXml.create_new(d["project"])
    said_topics = []
    used = {"types": [], "statuses": [], "labels": [], "users": [], "priorities": [], "stages": []}
    note = lambda kind, v: v and v not in used[kind] and used[kind].append(v)  # noqa: E731
    for t in d["topics"]:
        handler = bcf.add_topic(t["title"], t["description"], t["author"], t["topic_type"], t["topic_status"])
        topic = handler.topic
        topic.creation_date = _xml_date(t["date"])
        for k in ("priority", "stage", "assigned_to"):
            if t[k]:
                setattr(topic, k, t[k])
        if t["due_date"]:
            topic.due_date = _xml_date(t["due_date"])
        if t["labels"]:
            topic.labels = mdl.TopicLabels(label=list(t["labels"]))
        note("types", t["topic_type"])
        note("statuses", t["topic_status"])
        note("users", t["author"])
        note("priorities", t["priority"])
        note("stages", t["stage"])
        for x in t["labels"]:
            note("labels", x)
        if t["assigned_to"]:
            note("users", t["assigned_to"])
        # The header: the model files the topic is about.
        files = []
        for f in t["files"]:
            entry = mdl.File(filename=f["filename"].strip(), is_external=True)
            if f.get("date"):
                entry.date = _xml_date(_date(f["date"], "a file's date"))
            if f.get("reference"):
                entry.reference = str(f["reference"])
            gid = f.get("ifc_project") or (project_guid if open_file and f["filename"].strip() == open_file else None)
            if gid:
                entry.ifc_project = gid
            if f.get("ifc_spatial_structure_element"):
                entry.ifc_spatial_structure_element = f["ifc_spatial_structure_element"]
            files.append(entry)
        if files:
            handler.header = mdl.Header(files=mdl.HeaderFiles(file=files))
        # The viewpoint: the elements selected (and coloured), seen from a camera.
        aimed, found, missing = _aimed_camera(model, t["components"]) if t["camera"] is None else (None, [], [])
        if t["camera"] is not None:
            camera, how = _camera_model(t["camera"]), "given"
        elif aimed is not None:
            camera, how = _camera_model({"kind": "perspective", "position": aimed[0], "direction": aimed[1], "up": aimed[2], "aspect_ratio": 1280 / 960, "field_of_view": 60.0}), "aimed at its elements in the open model"
        else:
            import numpy as np
            from bcf.geometry import camera_vectors_from_target_position

            v = camera_vectors_from_target_position(np.zeros(3))
            camera, how = _camera_model({"kind": "perspective", "position": v[0], "direction": v[1], "up": v[2], "aspect_ratio": 1280 / 960, "field_of_view": 60.0}), "the model's origin (no open model holds its elements)"
        components = build_components(*t["components"])
        if t["colours"]:
            components.coloring = mdl.ComponentColoring(color=[mdl.ComponentColoringColor(color=c, components=mdl.ComponentColoringColorComponents(component=[mdl.Component(ifc_guid=g) for g in ids])) for c, ids in t["colours"]])
        vi = VisualizationInfoHandler(visualization_info=mdl.VisualizationInfo(guid=str(uuid.uuid4()), components=components, **camera))
        snapshot_name = None
        if t["snapshot"]:
            vi.snapshot, snapshot_name = t["snapshot"]
        vp = handler.add_visinfo_handler(vi, snapshot_filename=snapshot_name)
        if t["comments"]:
            topic.comments = mdl.TopicComments(
                comment=[mdl.Comment(date=_xml_date(c["date"]), author=c["author"], comment=c["comment"], guid=str(uuid.uuid4()), viewpoint=mdl.CommentViewpoint(guid=vp.guid)) for c in t["comments"]]
            )
            for c in t["comments"]:
                note("users", c["author"])
        entry: dict[str, Any] = {"guid": topic.guid, "title": t["title"], "topic_type": t["topic_type"], "topic_status": t["topic_status"], "components": len(t["components"]),
                                 "comments": len(t["comments"]), "camera": how, "snapshot": snapshot_name is not None}
        if t["colours"]:
            entry["coloured"] = sum(len(ids) for _, ids in t["colours"])
        if model is not None and t["camera"] is None and missing:
            entry["not_in_open_model"] = missing[:20]
            if len(missing) > 20:
                entry["not_in_open_model_count"] = len(missing)
        said_topics.append(entry)
    bcf.extensions = mdl.Extensions(
        topic_types=mdl.ExtensionsTopicTypes(topic_type=used["types"]),
        topic_statuses=mdl.ExtensionsTopicStatuses(topic_status=used["statuses"]),
        priorities=mdl.ExtensionsPriorities(priority=used["priorities"]) if used["priorities"] else None,
        topic_labels=mdl.ExtensionsTopicLabels(topic_label=used["labels"]) if used["labels"] else None,
        users=mdl.ExtensionsUsers(user=used["users"]),
        stages=mdl.ExtensionsStages(stage=used["stages"]) if used["stages"] else None,
    )
    path = f"{output_path}.bcf"
    try:
        # bcf-client prints a warning to stdout in one case; the server's stdout is the MCP stream.
        with contextlib.redirect_stdout(io.StringIO()):
            bcf.save(Path(path))
    except Exception as e:  # noqa: BLE001 - bcf-client's own reason
        _remove(path)
        raise BcfRefused(f"bcf-client could not write it: {' '.join(str(e).split())[:400] or type(e).__name__}") from e
    finally:
        bcf.close()
    errors = _check_parts(path)
    if errors:
        _remove(path)
        raise BcfRefused(f"it does not make a valid BCF 3.0 file: {errors[0]}" + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))
    # It reads back as the same BCF, through the library that wrote it.
    try:
        back = BcfXml.load(Path(path))
        topics = back.topics
        got = {g: (sorted(set(sum((vp.get_selected_guids() or [] for vp in th.viewpoints.values()), []))), len(th.comments)) for g, th in topics.items()}
        back.close()
    except Exception as e:  # noqa: BLE001
        _remove(path)
        raise BcfRefused(f"it did not read back as a BCF file: {' '.join(str(e).split())[:400]}") from e
    for t, entry in zip(d["topics"], said_topics):
        if got.get(entry["guid"]) != (sorted(t["components"]), len(t["comments"])):
            _remove(path)
            raise BcfRefused(f"it did not read back with the same elements and comments (topic {entry['title']!r})")
    with open(path, "rb") as f:
        data = f.read()
    return {
        "written": path,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "valid": True,
        "bcf_version": "3.0",
        "checked_against": "buildingSMART's BCF 3.0 XML schemas (github.com/buildingSMART/BCF-XML, tag v3.0), every part, and read back with bcf-client",
        "project": d["project"],
        "topics": said_topics,
        "written_by": f"bcf-client {_bcf_version()} (IfcOpenShell's BCF library), flo2-ifc {geometry._version()}",
    }

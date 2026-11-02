"""What changed between two building models, with IfcOpenShell's own IfcDiff, kept whole as a JSON file.

IfcDiff (IfcOpenShell's ``ifcdiff``, pinned in pyproject.toml) compares two
IFC models by GlobalId: an element only in the model after was ADDED, one only
in the model before was DELETED, and one in both is CHANGED when any of the
things it is told to compare differs. flo2-ifc runs IfcDiff's own comparison,
never a differ of its own, and only adds what a chat needs to say the change in
words:

- each element by GlobalId, IFC class and name, and how many of each class
  were added, deleted and changed;
- for an element whose attributes changed, WHICH attributes, with the value
  before and after (IfcDiff only says that they did): read off the two
  elements the way IfcDiff compares them, plain values only;
- for an element whose geometry changed, whether its PLACEMENT moved and by
  how much (IfcDiff's geometry check folds the placement into the shape);
- for an element whose properties changed, IfcDiff's own record of which
  property sets and values changed (a DeepDiff), as it reports it.

THE DEFAULTS, from IfcDiff's options:

- ``compare``: everything IfcDiff can compare (``COMPARE``): the element's
  attributes, its properties, its type, its container, what it is part of, its
  classifications and its geometry. IfcDiff's own default is attributes and
  geometry only, which says nothing of a changed fire rating or a wall moved
  to another storey: the things a person most often changes. A chat can narrow
  it (``compare`` "attributes, property" leaves the geometry out, the slowest
  part).
- ``is_shallow`` False: every kind of change an element has is reported, not
  only the first IfcDiff meets, so a wall renamed AND moved says both. IfcDiff
  stops at the first relationship that differs even then, so each relationship
  is asked separately (``_EveryRelationship``), with IfcDiff's own check.
- ``filter_elements`` (``selector``): none, so every element IfcDiff compares
  by default (every IfcElement and spatial element, openings left out).

The comparison runs in a forked CHILD, as the IDS check does: it opens the
second model there, runs IfcDiff, writes the whole comparison to
``<output_path>.json`` and sends back only what one reply can carry. The child
is stopped at the call's time limit, and a child the memory cap stops is
reported as such: the session, and the model it holds, survive both. Two
models too large to compare within the helper's memory are refused BEFORE
anything is opened (``MAX_BYTES``, set from measured peaks).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import select as _io_select
import time
from typing import Any

import ifcopenshell

from flo2_ifc import geometry, reading

#: What IfcDiff can compare (its RELATIONSHIP_TYPE), and ``placement``, IfcDiff's quick geometry check, in the order
#: each is asked.
COMPARE = ("attributes", "property", "type", "container", "aggregate", "classification", "placement", "geometry")
#: What is compared when the caller names nothing: everything IfcDiff compares, its shapes too, for two models at most
#: GEOMETRY_MAX_BYTES together; for larger ones the placements (IfcDiff's quick check) in place of the shapes, which
#: would not fit one call or the helper's memory (measured: README.md, "What changed between two models"). The
#: placements are compared with the shapes too: IfcDiff's shape check takes only an element with a "Body" shape in a
#: model view, so it missed three elements moved in a Revit export (measured 2026-11-02 on the public
#: Clinic_Architectural sample: 7 changed with shapes alone, 10 with placements).
DEFAULT_COMPARE = ("attributes", "property", "type", "container", "aggregate", "classification", "placement", "geometry")
DEFAULT_COMPARE_LARGE = ("attributes", "property", "type", "container", "aggregate", "classification", "placement")
#: What each says, for a reply.
COMPARE_SAYS = {
    "attributes": "the element's own attributes (its name, description, object type, tag, a door's sizes)",
    "property": "its property and quantity sets and their values",
    "type": "its type",
    "container": "the storey or space that contains it",
    "aggregate": "what it is part of",
    "classification": "its classification references",
    "placement": "where it is placed and its openings (IfcDiff's quick geometry check, no shapes made)",
    "geometry": "its shape and where it is placed (IfcDiff's full check: every shape made in both models)",
}
#: The names IfcDiff gives each kind of change in its report, and the kind as a reply says it.
CHANGE_KINDS = {
    "attributes_changed": "attributes",
    "properties_changed": "properties",
    "type_changed": "type",
    "container_changed": "container",
    "aggregate_changed": "aggregate",
    "classification_changed": "classification",
    "placement_changed": "placement",
    "geometry_changed": "geometry",
}
#: The two models together, in bytes, beyond which a comparison is refused before
#: anything is opened: it may not fit the helper's memory (flo2's broker caps a
#: session at 1152 MiB). Set from peaks measured with tools/measure.py --diff under
#: the broker's flags (README.md, "What changed between two models"): the largest
#: pair measured, two public samples of 49.3 and 48.2 MB (785,000 entities each),
#: peaked at 758 MiB; two of 31.6 and 31.0 MB at 564 MiB. A session that has
#: already made meshes (a measure, a drawing) holds them as well.
MAX_BYTES = int(os.environ.get("FLO2_IFC_DIFF_MAX_BYTES", "100000000"))
#: The two models together, in bytes, up to which their SHAPES are compared by default (IfcDiff's full geometry
#: check, which makes every shape of both); above it the default compares placements instead, and a call that asks for
#: the shapes is refused unless `selector` narrows it. Set from measured comparisons (README.md, as above).
GEOMETRY_MAX_BYTES = int(os.environ.get("FLO2_IFC_DIFF_GEOMETRY_MAX_BYTES", str(8 * 1024 * 1024)))
#: Changed, added or deleted elements one reply lists of each, at most, when the
#: caller names no limit; the kept file holds them all.
DEFAULT_LIMIT = 50
#: Characters one changed element's details may spend in a reply (the kept file
#: holds them whole).
DETAIL_CHARS = 600
#: The share of the call's time the comparison may use, its file included.
USE_OF_BUDGET = 0.95
#: Geometry threads IfcDiff's shape check may start. IfcDiff asks for one per CPU
#: the host has; flo2's broker gives a session ONE CPU, and each thread holds
#: shapes of its own.
THREADS = int(os.environ.get("FLO2_IFC_DIFF_THREADS", "1"))
#: What the kept file says it is, so a program reading it knows its shape.
FORMAT = "flo2-ifc ifc_diff 1"


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


class DiffRefused(ValueError):
    """A comparison that cannot be made, with the reason a chat can correct."""


def _ifcdiff_version() -> str:
    try:
        from importlib.metadata import version

        return version("ifcdiff")
    except Exception:  # noqa: BLE001
        return "unknown"


def _is_ifc_text(path: str) -> bool:
    """Whether `path` starts as an IFC-SPF file does (ISO 10303-21's first line)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").upper().startswith(b"ISO-10303-21")


def _size(path: str | None) -> int | None:
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def parse_compare(compare: str | list[str] | None, together: int = 0) -> list[str]:
    """`compare` as IfcDiff's relationships, in COMPARE's order; when empty, the default for two models of `together`
    bytes."""
    if compare is None or compare == "" or compare == []:
        return list(DEFAULT_COMPARE if together <= GEOMETRY_MAX_BYTES else DEFAULT_COMPARE_LARGE)
    names = [c.strip().lower() for c in (compare.split(",") if isinstance(compare, str) else compare) if str(c).strip()]
    aliases = {"attribute": "attributes", "properties": "property", "props": "property", "shape": "geometry", "shapes": "geometry", "placements": "placement"}
    names = [aliases.get(n, n) for n in names]
    unknown = [n for n in names if n not in COMPARE]
    if unknown:
        raise DiffRefused(f"`compare` names {', '.join(unknown)}; IfcDiff compares {', '.join(COMPARE)} (comma separated; leave it out for all)")
    return [c for c in COMPARE if c in names]


def _attribute_changes(old: ifcopenshell.entity_instance, new: ifcopenshell.entity_instance, precision: float) -> dict[str, Any]:
    """Which plain attributes differ, with each value before and after: the attributes IfcDiff compares (its
    diff_element leaves out those that are entities or lists), named."""
    out: dict[str, Any] = {}
    for i in range(len(new)):
        name = new.attribute_name(i)
        try:
            a, b = old[i], new[i]
        except (IndexError, RuntimeError):
            continue
        if isinstance(a, (ifcopenshell.entity_instance, tuple)) or isinstance(b, (ifcopenshell.entity_instance, tuple)):
            continue
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and abs(float(a) - float(b)) <= precision:
            continue
        if a != b and not (str(a) == str(b)):
            out[name] = {"before": a, "after": b}
    return out


def _placement_change(old: ifcopenshell.entity_instance, new: ifcopenshell.entity_instance, precision: float) -> dict[str, Any] | None:
    """Whether the element's placement moved or turned, read with IfcOpenShell's placement utility at IfcDiff's own
    tolerances (its diff_element_basic_geometry), the move in the model's length unit."""
    import numpy as np
    import ifcopenshell.util.placement

    try:
        a = ifcopenshell.util.placement.get_local_placement(old.ObjectPlacement)
        b = ifcopenshell.util.placement.get_local_placement(new.ObjectPlacement)
    except Exception:  # noqa: BLE001 - an element with no placement, or one IfcOpenShell cannot read
        return None
    moved = not np.allclose(a[:, 3], b[:, 3], atol=precision)
    turned = not np.allclose(a[0:3, 0:3], b[0:3, 0:3], atol=1e-2)
    if not (moved or turned):
        return {"placement_changed": False}
    out: dict[str, Any] = {"placement_changed": True}
    if moved:
        # get_local_placement answers in the model's own length unit.
        out["moved_by"] = [round(float(x), 6) for x in (b[:3, 3] - a[:3, 3])]
    if turned:
        out["turned"] = True
    return out


def _deepdiff_plain(diff: Any) -> Any:
    """IfcDiff's record of a property change (a DeepDiff, or True) as plain JSON."""
    if diff is True or diff is None:
        return diff
    to_json = getattr(diff, "to_json", None)
    if callable(to_json):
        try:
            return json.loads(to_json())
        except Exception:  # noqa: BLE001
            pass
    try:
        return json.loads(json.dumps(diff, default=lambda o: list(o) if isinstance(o, (set, frozenset)) else str(o)))
    except Exception:  # noqa: BLE001
        return str(diff)


def _brief(e: ifcopenshell.entity_instance | None, global_id: str) -> dict[str, Any]:
    if e is None:
        return {"global_id": global_id}
    out: dict[str, Any] = {"global_id": global_id, "class": e.is_a()}
    name = getattr(e, "Name", None)
    if name:
        out["name"] = name
    return out


def _without_ids(value: Any) -> Any:
    """Property sets as IfcOpenShell's get_psets gives them, without the STEP ids it adds (`id`)."""
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return [_without_ids(v) for v in value]
    return value


def _ifcdiff_class():
    """IfcDiff, with IfcDiff's own comparisons, asked so a whole model fits one call:

    - each relationship is asked separately (IfcDiff stops at the first that differs);
    - an element whose plain attributes, or property sets, are EQUAL in both models is not handed to DeepDiff, which
      would find nothing (most elements of a model, and nine tenths of the time: 25 of 30 s on a 13 MB model);
    - a property set's own STEP id is left out of the comparison, as IfcDiff means to: its
      ``exclude_regex_paths=[r".*id$"]`` matches no path DeepDiff 9 writes (``root['Pset_X']['id']`` ends in ``']``),
      so without this every property set of a file whose entities are numbered differently (another export of the
      same model) reads as changed. Measured 2026-11-02 (tests/test_diffing.py, a model renumbered);
    - ``placement`` is IfcDiff's quick geometry check (``diff_element_basic_geometry``: the placement moved or turned,
      or the openings and projections changed), which IfcDiff has but does not run;
    - shapes are summarised in batches (one geometry iterator over a whole large model holds every shape it made
      until it is dropped: reading.GEOMETRY_BATCH)."""
    from deepdiff import DeepDiff
    from ifcdiff import IfcDiff
    import ifcopenshell.util.element

    class _Flo2IfcDiff(IfcDiff):
        def diff_element(self, old, new):
            plain = lambda e: [a for a in e if not isinstance(a, (ifcopenshell.entity_instance, tuple))]  # noqa: E731 - as IfcDiff's diff_element
            if plain(old) == plain(new):
                return None
            return super().diff_element(old, new)

        def diff_element_relationships(self, old, new):
            asked = self.relationships
            found = False
            try:
                for r in [r for r in asked if r not in ("attributes", "geometry")]:
                    if r == "property":
                        found = self._diff_properties(old, new) or found
                    elif r == "placement":
                        found = self._diff_placement(old, new) or found
                    else:
                        self.relationships = [r]
                        found = bool(super().diff_element_relationships(old, new)) or found
            finally:
                self.relationships = asked
            return found

        def _diff_properties(self, old, new):
            # IfcDiff's own property comparison (diff_element_relationships, "property"), its STEP ids left out.
            old_psets = _without_ids(ifcopenshell.util.element.get_psets(old))
            new_psets = _without_ids(ifcopenshell.util.element.get_psets(new))
            if old_psets == new_psets:
                return False
            try:
                diff = DeepDiff(old_psets, new_psets, math_epsilon=self.precision, ignore_string_type_changes=True, ignore_numeric_type_changes=True)
            except Exception:  # noqa: BLE001 - as IfcDiff: a comparison that fails counts as a change
                diff = True
            if diff and new.GlobalId:
                self.change_register.setdefault(new.GlobalId, {}).update({"properties_changed": diff})
                return True
            return False

        def _diff_placement(self, old, new):
            if old.ObjectPlacement is None and new.ObjectPlacement is None:
                return False
            try:
                changed = old.ObjectPlacement is None or new.ObjectPlacement is None or bool(self.diff_element_basic_geometry(old, new))
            except Exception:  # noqa: BLE001 - a placement IfcOpenShell cannot read counts as a change, as IfcDiff counts one
                changed = True
            if changed and new.GlobalId:
                self.change_register.setdefault(new.GlobalId, {}).update({"placement_changed": True})
            return changed

        def summarise_shapes(self, ifc, elements):
            shapes: dict[str, Any] = {}
            batch = max(1, reading.GEOMETRY_BATCH)
            for i in range(0, len(elements), batch):
                shapes.update(super().summarise_shapes(ifc, elements[i : i + batch]))
            return shapes

    return _Flo2IfcDiff


def _compare(before: ifcopenshell.file, after: ifcopenshell.file, compare: list[str], selector: str | None) -> Any:
    """IfcDiff run on the two models, its own prints kept out of the server's stdout (the MCP stream)."""
    import ifcdiff

    cls = _ifcdiff_class()
    # IfcDiff starts one geometry thread per CPU of the HOST; a session has one CPU.
    ifcdiff.multiprocessing.cpu_count = lambda: max(1, THREADS)  # type: ignore[attr-defined]
    d = cls(before, after, relationships=list(compare), is_shallow=False, filter_elements=selector or None)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        d.diff()
    # How many elements both models hold: IfcDiff counts them and says so, and keeps only the added and deleted.
    common = re.search(r"(\d+) item\(s\) are common to both models", said.getvalue())
    d.common_elements = int(common.group(1)) if common else None
    return d


def compared(before: ifcopenshell.file, after: ifcopenshell.file, compare: list[str], selector: str | None = None) -> dict[str, Any]:
    """The whole comparison as plain data: IfcDiff's own report (`ifcdiff`, as its export writes it) and, beside it,
    every element by GlobalId, class and name, what changed on each, and the counts by class."""
    d = _compare(before, after, compare, selector)
    added = sorted((_brief(after.by_guid(g), g) for g in d.added_elements), key=_order)
    deleted = sorted((_brief(before.by_guid(g), g) for g in d.deleted_elements), key=_order)
    changed = []
    for gid, said in d.change_register.items():
        try:
            new = after.by_guid(gid)
            old = before.by_guid(gid)
        except RuntimeError:
            new = old = None
        entry = _brief(new, gid)
        entry["changed"] = [CHANGE_KINDS.get(k, k) for k in said if k in CHANGE_KINDS or k.endswith("_changed")]
        if old is not None and new is not None:
            if "attributes_changed" in said:
                entry["attributes"] = _attribute_changes(old, new, d.precision)
                if getattr(old, "Name", None) != getattr(new, "Name", None):
                    entry["name_before"] = getattr(old, "Name", None)
            if "properties_changed" in said:
                entry["properties"] = _deepdiff_plain(said["properties_changed"])
            if "geometry_changed" in said or "placement_changed" in said:
                placed = _placement_change(old, new, d.precision)
                if placed:
                    entry["placement"] = placed
        changed.append(entry)
    changed.sort(key=_order)
    by_class: dict[str, dict[str, int]] = {}
    for kind, items in (("added", added), ("deleted", deleted), ("changed", changed)):
        for e in items:
            c = by_class.setdefault(e.get("class", "?"), {"added": 0, "deleted": 0, "changed": 0})
            c[kind] += 1
    common = d.common_elements
    counts = {
        "before": None if common is None else len(deleted) + common,
        "after": None if common is None else len(added) + common,
        "added": len(added),
        "deleted": len(deleted),
        "changed": len(changed),
        "unchanged": None if common is None else common - len(changed),
    }
    own = {
        "added": sorted(d.added_elements),
        "deleted": sorted(d.deleted_elements),
        "changed": {g: {k: _deepdiff_plain(v) for k, v in c.items()} for g, c in d.change_register.items()},
    }
    return {
        "counts": counts,
        "by_class": dict(sorted(by_class.items())),
        "added": added,
        "deleted": deleted,
        "changed": changed,
        "precision": d.precision,
        "ifcdiff": own,
    }


def _order(e: dict[str, Any]) -> tuple[str, str, str]:
    return (e.get("class", ""), str(e.get("name") or ""), e["global_id"])


def _model_entry(model: ifcopenshell.file, path: str | None, edits: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {"file": os.path.basename(path) if path else None, "sha256": geometry.file_sha256(path), "schema": model.schema}
    if not path:
        out["note"] = "This model was made in the session (ifc_new) and has no file."
    elif edits:
        out["edited"] = f"The session's copy was changed {edits} time(s) after it was opened, so this is the EDITED copy, not the file the sha256 names."
    return out


def _short(entry: dict[str, Any]) -> dict[str, Any]:
    """One changed element as a reply carries it: its details cut to DETAIL_CHARS (the kept file holds them whole)."""
    out = {k: v for k, v in entry.items() if k not in ("attributes", "properties", "placement")}
    for k in ("attributes", "placement", "properties"):
        if k not in entry:
            continue
        text = json.dumps(entry[k], default=str)
        if len(text) <= DETAIL_CHARS:
            out[k] = entry[k]
        elif k == "properties" and isinstance(entry[k], dict):
            # The paths that changed, without their values.
            out[k] = {kind: (sorted(v)[:10] if isinstance(v, (list, dict)) else v) for kind, v in entry[k].items()}
            out["details_cut"] = True
        else:
            out[k] = text[:DETAIL_CHARS] + "..."
            out["details_cut"] = True
    return out


def _run_child(model: ifcopenshell.file, other_path: str, other_is: str, compare: list[str], selector: str | None, output_path: str, header: dict[str, Any], offset: int, limit: int, budget: float) -> tuple[dict[str, Any] | None, str]:
    """The comparison in a forked child. Returns its reply-sized summary (None when it did not finish) and how it
    ended: "done", "time" (stopped at the limit), "killed" (the memory cap stopped it) or the failure."""
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child
        code = 0
        try:
            os.close(read_end)
            # IfcDiff and IfcOpenShell print; the server's stdout is the MCP stream.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            w = os.fdopen(write_end, "wb", buffering=0)
            other = ifcopenshell.open(other_path)
            before, after = (model, other) if other_is == "after" else (other, model)
            found = compared(before, after, compare, selector)
            whole = {"format": FORMAT, **header, **found}
            path = f"{output_path}.json"
            data = json.dumps(whole, indent=1, ensure_ascii=False, default=str).encode()
            with open(path, "wb") as f:
                f.write(data)
            pages = {}
            for kind in ("added", "deleted", "changed"):
                items = found[kind] if kind != "changed" else [_short(e) for e in found[kind]]
                page, info = reading.page_of(items, offset, limit, chars=reading.PAGE_CHARS // 3)
                pages[kind] = {"items": page, "page": info}
            summary = {
                "counts": found["counts"],
                "by_class": found["by_class"],
                "pages": pages,
                "written": path,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "schemas": [before.schema, after.schema],
            }
            w.write((json.dumps({"end": summary}, default=str) + "\n").encode())
        except BaseException as e:  # noqa: BLE001 - the child reports any failure
            code = 1
            try:
                w.write((json.dumps({"failed": f"{type(e).__name__}: {' '.join(str(e).split())[:600]}", "refused": isinstance(e, DiffRefused)}) + "\n").encode())
            except BaseException:  # noqa: BLE001
                pass
        finally:
            os._exit(code)
    os.close(write_end)
    buf = b""
    deadline = time.monotonic() + max(0.0, budget)
    timed_out = False
    with os.fdopen(read_end, "rb", buffering=0) as r:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = _io_select.select([r], [], [], left)
            if not ready:
                continue
            chunk = r.read(1 << 16)
            if not chunk:
                break
            buf += chunk
    if timed_out:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    _, status = os.waitpid(pid, 0)
    for line in buf.split(b"\n"):
        if not line.strip():
            continue
        try:
            said = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "end" in said:
            return said["end"], "done"
        if "failed" in said:
            if said.get("refused"):
                raise DiffRefused(said["failed"].split(": ", 1)[-1])
            return None, said["failed"]
    if timed_out:
        return None, "time"
    if os.WIFSIGNALED(status) and os.WTERMSIG(status) == 9:
        return None, "killed"
    return None, "it ended without an answer"


def diff(
    model: ifcopenshell.file,
    model_path: str | None,
    other_path: str,
    output_path: str,
    other_is: str = "after",
    compare: str | list[str] = "",
    selector: str = "",
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Compare the model open in the session with the model at `other_path` (the model AFTER it, or BEFORE it with
    ``other_is`` "before"), write the whole comparison to `output_path`.json, and answer what one reply can carry."""
    other_is = (other_is or "after").strip().lower()
    if other_is not in ("after", "before"):
        raise DiffRefused(f'`other_is` is "after" (the other model is the one after this one, the default) or "before", not {other_is!r}')
    if offset < 0:
        raise DiffRefused("offset is where each list starts, 0 or more")
    if limit < 1:
        raise DiffRefused("limit is how many of each list to give, 1 or more")
    limit = min(limit, reading.MAX_LIMIT)
    parse_compare(compare)  # refuse a name IfcDiff does not compare before anything else
    if not other_path or not os.path.isfile(other_path):
        raise DiffRefused(f"there is no model at {other_path!r} to compare with")
    if not _is_ifc_text(other_path):
        raise DiffRefused(f"{os.path.basename(other_path)!r} is not an IFC model: an IFC file is text that starts with ISO-10303-21 (an .ifc file, not .ifczip or .ifcxml)")
    if model_path and os.path.abspath(model_path) == os.path.abspath(other_path) and not edits:
        raise DiffRefused("the two models are the same file, with no edit made to the open one: there is nothing to compare")
    own, theirs = _size(model_path), _size(other_path) or 0
    together = (own or 0) + theirs
    mb = _mb
    if together > MAX_BYTES:
        raise DiffRefused(
            f"the two models are too large to compare within this helper's memory: {mb(own or 0)} and {mb(theirs)}, {mb(together)} together, "
            f"and the largest pair measured to fit is {mb(MAX_BYTES)} together. Nothing was opened. Compare part of them with `selector` "
            f"(e.g. \"IfcWall, IfcDoor\"), or compare them on a machine with more memory with IfcOpenShell's ifcdiff"
        )
    mb = _mb
    selector = (selector or "").strip()
    wanted = parse_compare(compare, together)
    if "geometry" in wanted and together > GEOMETRY_MAX_BYTES and not selector:
        raise DiffRefused(
            f"comparing the shapes of two models this size ({mb(together)} together) does not fit this helper: IfcDiff makes every shape of both, "
            f"which takes longer than one call and more memory than the helper has above {mb(GEOMETRY_MAX_BYTES)} together. Nothing was opened. "
            f"Compare placements instead (`compare` with placement in place of geometry, the default for models this size), "
            f"or name the elements whose shapes to compare with `selector` (e.g. \"IfcWall\")"
        )
    if selector:
        from ifcopenshell.util import selector as selectors

        try:
            selectors.filter_elements(model, selector)
        except Exception as e:  # noqa: BLE001 - the selector's own parse error
            raise DiffRefused(f"`selector` {selector!r} is not a selector IfcOpenShell reads: {' '.join(str(e).split())[:300]}") from e
    started = time.monotonic()
    budget = (reading.SECONDS if seconds is None else seconds) * USE_OF_BUDGET
    this = _model_entry(model, model_path, edits)
    that = {"file": os.path.basename(other_path), "sha256": geometry.file_sha256(other_path)}
    before, after = (this, that) if other_is == "after" else (that, this)
    header = {
        "before": before,
        "after": after,
        "options": {"compare": wanted, "is_shallow": False, "selector": selector or None},
        "compared_by": f"IfcDiff {_ifcdiff_version()} (IfcOpenShell's ifcdiff), ifcopenshell {ifcopenshell.version}, flo2-ifc {geometry._version()}",
    }
    if not compare and "geometry" not in wanted:
        header["note_geometry"] = (
            f"The shapes were not compared: two models this size ({mb(together)} together, over {mb(GEOMETRY_MAX_BYTES)}) take longer than one call "
            "and more memory than this helper has for IfcDiff to make every shape of both. Placements and openings were compared (IfcDiff's quick "
            "geometry check), so an element moved or turned is found; a shape changed in place is not. To compare shapes, name some elements "
            "with `selector` and `compare` with geometry."
        )
    if not hasattr(os, "fork"):  # pragma: no cover - flo2-ifc runs on Linux
        raise RuntimeError("the comparison needs os.fork")
    summary, how = _run_child(model, other_path, other_is, wanted, selector or None, output_path, header, offset, limit, budget)
    took = round(time.monotonic() - started, 1)
    if summary is None:
        if how == "time":
            raise DiffRefused(
                f"the comparison did not finish within {budget:.0f} s, this call's time, and was stopped; nothing was kept. "
                f"Compare less: leave the shapes out (`compare` \"attributes, property, type, container, aggregate, classification, placement\"), "
                f"or compare part of the models with `selector` (e.g. \"IfcWall, IfcDoor\")"
            )
        if how == "killed":
            raise DiffRefused(
                "the comparison ran out of this helper's memory and was stopped; nothing was kept, and the model open in this session is unchanged. "
                "Compare part of the models with `selector`, or leave the shapes out with `compare` (placement in place of geometry)"
            )
        raise RuntimeError(f"the comparison could not run: {how}")
    if summary["schemas"][0] != summary["schemas"][1]:
        header["note_schemas"] = (
            f"The two models are of different IFC versions ({summary['schemas'][0]} before, {summary['schemas'][1]} after): "
            "an element exported again in the other version can read as changed where only the version differs."
        )
    counts = summary["counts"]
    out: dict[str, Any] = {
        **header,
        "complete": True,
        "counts": counts,
        "by_class": summary["by_class"],
    }
    withheld = []
    for kind in ("added", "deleted", "changed"):
        p = summary["pages"][kind]
        out[kind] = p["items"]
        if p["page"]["withheld"]:
            out[f"{kind}_page"] = p["page"]
            withheld.append(f"{p['page']['listed']} of {p['page']['total']} {kind}")
    out["written"] = summary["written"]
    out["bytes"] = summary["bytes"]
    out["sha256"] = summary["sha256"]
    out["say"] = (
        f"{counts['added']} added, {counts['deleted']} deleted and {counts['changed']} changed, of {counts['before']} element(s) before "
        f"and {counts['after']} after (compared: {', '.join(wanted)})."
    )
    if withheld:
        out["more"] = (
            f"This reply lists {', '.join(withheld)}; the whole comparison, every element and what changed on it, is in the kept file. "
            f"Call again with offset {offset + limit} for the next ones."
        )
    out["seconds"] = took
    return out

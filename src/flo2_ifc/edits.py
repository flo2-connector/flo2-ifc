"""What the session's edits touched, so a check of an edit checks the edit and not the whole model (since 0.12.0).

flo2's design 1a97fc1f1de4d5d2, dec:a-save-checks-only-what-the-edits-touched-and-a-file-versions-own-check-is-kept
(accepted 2026-11-05), from fact:root-cause-a-large-models-save-costs-a-chat-three-calls-every-time-because-the-
check-is-of-the-whole-model-not-the-edit-2026-11-05: a save of an edited ~500,000-instance model cost a chat three
calls of 30 s, because the check that judges what the edits did ran over the whole model, however small the edit.

THE MECHANISM IS IFCOPENSHELL'S OWN UNDO RECORD. Every change to a model goes through ``ifcopenshell.file``'s open
transaction when there is one: ``create_entity`` and ``add`` call ``store_create``, an attribute set
(``entity_instance.__setitem__``, which ``setattr`` uses) calls ``store_edit`` with the old and new value, and
``remove`` calls ``store_delete`` with the removed instance's attributes and, before it goes, every instance that
references it (its inverses), which IfcOpenShell then edits in C++ without a ``store_edit`` (ifcopenshell/file.py,
entity_instance.py, 0.9.0). ifcopenshell.api works only through those. So each call of a tool that changes the model
(``CHANGES``) runs with a transaction of the record's own open (``Record.change``), and what it holds when the call
ends is read into the record and dropped: created, changed (edited, or left referring to nothing by a removal) and
removed, with what each referenced before.

WHAT IT DOES NOT SEE, and how each is caught rather than missed:

- a change made with no transaction open: one that IfcOpenShell's C++ makes directly (``file._remove``, a raw
  ``set_attribute_value``), which nothing in ifcopenshell.api, ifcmcp or flo2-ifc calls. Each tool that changes the
  model is counted (the model's instances before and after against those the record saw made and removed) and each
  that does not is watched (its highest step id), so one that moved the model behind the record's back is caught, and
  the record is then refused by name until the model is opened again (``Unknown``);
- a transaction opened INSIDE a call by something else (``begin_transaction`` replaces the record's, and after its
  ``end_transaction`` none is open): only ifcopenshell.api.alignment does that (``_get_segment_endpoint``). It is
  caught (the record's transaction is no longer the open one when the call ends) and refused the same way. flo2-ifc's
  own steps (building.py, ``_hold_units``) take a ``Step`` inside the record's transaction instead of opening one;
- an attribute edit by a tool that only reads: no reader in ifcmcp or flo2-ifc writes the model (ifc_render draws a
  type on a copy), and a reader's edit that adds or removes nothing is not caught;
- the model replaced without ``ifc_load``, ``ifc_new`` or ``ifc_reset`` (``REPLACES``): the record names the model it
  began with, and refuses another.

THE NEIGHBOURS (``Record.scope``). An instance's schema check (ifcopenshell.validate, without the rules) reads its
own attributes, the class of each instance they name, and how many instances name it through each inverse
attribute. An edit can change those for an instance it never touched: a removed association leaves the usage it named
with none (an inverse ``SET [1:?]`` fails); a deleted element empties a required attribute of every instance that named
it. So the check takes every instance the edits created or changed, what each references and what references it now,
and what the changed and removed ones referenced before the edits (held from the transaction at the time), and every
instance a removed one was referenced by. The whole model's GlobalIds are checked every call (reading.validate).
tests/test_edits_class.py holds this to finding every error the whole-model check would, element by element.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import ifcopenshell
import ifcopenshell.ifcopenshell_wrapper
from ifcopenshell.file import Transaction

#: The tools whose calls change the session's model: ifcmcp's editing three and flo2-ifc's building steps. Each call
#: is recorded.
CHANGES = (
    "ifc_edit",
    "ifc_shape",
    "ifc_quantify",
    "ifc_add_wall",
    "ifc_add_slab",
    "ifc_add_roof",
    "ifc_add_space",
    "ifc_add_unit",
)
#: The tools that replace the session's model, after which the record starts again (or holds none).
REPLACES = ("ifc_load", "ifc_new", "ifc_reset")
#: Every other tool the server serves: none changes the session's model. Each call is watched for one that does.
#: server.build_server refuses to start with a tool in none of the three, so a new tool is classed before it is served.
READS = (
    "ifc_save",
    "ifc_summary",
    "ifc_tree",
    "ifc_info",
    "ifc_select",
    "ifc_relations",
    "ifc_clash",
    "ifc_contexts",
    "ifc_materials",
    "ifc_list",
    "ifc_docs",
    "ifc_validate",
    "ifc_schedule",
    "ifc_cost",
    "ifc_schema",
    "ifc_shape_list",
    "ifc_shape_docs",
    "ifc_plot",
    "ifc_render",
    "ifc_extent",
    "ifc_units",
    "ifc_footprint",
    "ifc_volume",
    "ifc_distance",
    "ifc_draw",
    "ifc_check_ids",
    "ifc_write_ids",
    "ifc_check_rules",
    "ifc_diff",
    "ifc_write_bcf",
    "ifc_edited",
    "ifc_plan_set",
)
#: What ifc_edited lists by ``how``: one kind, the edits' own three (the default, ""), or what scope "edits" checks.
HOW = ("created", "changed", "removed", "neighbour", "checked")


class Unknown(ValueError):
    """What the edits touched cannot be known; the whole model is checked instead."""


def _say_unknown(why: str) -> str:
    return (
        f'scope "edits" is refused: {why}. What the edits touched is not known, so check the whole model with scope'
        ' "model" (the default); the record starts again when the model is opened again.'
    )


def _ids_in(value: Any) -> set[int]:
    """The step ids a value serialised by IfcOpenShell's Transaction names ({"id": n}, nested in tuples)."""
    out: set[int] = set()
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            if v.get("id"):
                out.add(v["id"])
            else:
                stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return out


def _alive(model: ifcopenshell.file, step_id: int) -> ifcopenshell.entity_instance | None:
    try:
        return model.by_id(step_id)
    except RuntimeError:
        return None


def element_of(inst: Any) -> dict[str, Any] | None:
    """An instance as an issue or a listing names it: its step id, its IFC class and its GlobalId (None without one)."""
    if not isinstance(inst, ifcopenshell.entity_instance):
        return None
    try:
        step_id = inst.id()
        ifc_class = inst.is_a()
    except Exception:  # noqa: BLE001 - an instance removed since it was named
        return None
    if not step_id:
        return None
    try:
        global_id = getattr(inst, "GlobalId", None)
    except Exception:  # noqa: BLE001 - a GlobalId whose value cannot be read
        global_id = None
    return {"id": step_id, "type": ifc_class, "global_id": global_id if isinstance(global_id, str) else None}


@dataclass
class Scope:
    """What ``ifc_validate`` with scope "edits" checks: the step ids, sorted, and what they are."""

    ids: list[int]
    counts: dict[str, int]
    since: dict[str, Any]


@dataclass
class Record:
    """What the edits of the session's model created, changed and removed since it was opened (``opened``)."""

    model: ifcopenshell.file | None = None
    since: dict[str, Any] = field(default_factory=dict)
    created: set[int] = field(default_factory=set)
    changed: set[int] = field(default_factory=set)
    #: Removed instances the opened model held, each as it was: {"id", "type", "global_id"}.
    removed: dict[int, dict[str, Any]] = field(default_factory=dict)
    #: Instances the edits' instances referenced before an edit or a removal, or were referenced by when removed.
    referenced: set[int] = field(default_factory=set)
    #: Instances the edits made and removed again: never in the opened model, never listed.
    gone: set[int] = field(default_factory=set)
    #: Why the record cannot say what the edits touched, once it cannot.
    why: str | None = None

    # ---------------------------------------------------------------- the model opened

    def opened(self, model: ifcopenshell.file | None, by: str, path: str | None) -> None:
        """Start again for the model the session now holds (None after ifc_reset)."""
        self.model = model
        self.since = {"opened": path, "by": by}
        self.created, self.changed, self.removed, self.referenced, self.gone = set(), set(), {}, set(), set()
        self.why = None if model is not None else "no model is open"

    def lost(self, why: str) -> None:
        """From now until the model is opened again, the record cannot say what the edits touched."""
        if self.why is None:
            self.why = why

    # ---------------------------------------------------------------- the calls

    def change(self, model: ifcopenshell.file | None, tool: str, call: Callable[[], Any]) -> Any:
        """Run a call of a tool that changes the model, recording what it creates, changes and removes."""
        if model is None:
            return call()
        if model is not self.model:
            self.lost(f"the session's model is not the one opened when the record began, so {tool} changed a model it did not see opened")
        outer = model.transaction
        mine = outer is None
        tx = Transaction(model) if mine else outer
        start = len(tx.operations)
        count = len(model.entity_names())
        if mine:
            model.transaction = tx
        try:
            return call()
        finally:
            displaced = model.transaction is not tx
            if mine and not displaced:
                model.transaction = None
            ops = tx.operations[start:]
            if mine:
                tx.operations = []
            made = self._read(ops)
            if displaced:
                self.lost(f"{tool} opened an IfcOpenShell transaction of its own during the call, so what it changed after that was not recorded")
            elif len(model.entity_names()) - count != made:
                self.lost(f"{tool} added or removed instances the record did not see")

    def read(self, model: ifcopenshell.file | None, tool: str, call: Callable[[], Any]) -> Any:
        """Run a call of a tool that only reads, and lose the record if it added to the model anyway."""
        if model is None:
            return call()
        highest = model.get_max_id()
        try:
            return call()
        finally:
            if model.get_max_id() != highest:
                self.lost(f"{tool}, which only reads, added instances to the model")

    def _read(self, ops: list[dict[str, Any]]) -> int:
        """Read one call's transaction into the record; returns how many instances it added less those it removed."""
        net = 0
        for op in ops:
            action = op["action"]
            if action == "create":
                step_id = op["value"]["id"]
                net += 1
                if step_id in self.removed:  # put back with its own id: a class reassigned, an undo
                    del self.removed[step_id]
                    self.changed.add(step_id)
                else:
                    self.gone.discard(step_id)
                    self.created.add(step_id)
            elif action == "edit":
                if op["id"] not in self.created:
                    self.changed.add(op["id"])
                self.referenced |= _ids_in(op["old"]) | _ids_in(op["new"])
            elif action == "delete":
                value = op["value"]
                step_id = value["id"]
                net -= 1
                self.referenced |= _ids_in({k: v for k, v in value.items() if k != "id"})
                self._referrers_lost(op.get("inverses") or {})
                if step_id in self.created:
                    self.created.discard(step_id)
                    self.gone.add(step_id)
                else:
                    self.changed.discard(step_id)
                    self.removed[step_id] = {"id": step_id, "type": value.get("type"), "global_id": value.get("GlobalId") if isinstance(value.get("GlobalId"), str) else None}
            elif action == "batch_delete":
                self._referrers_lost(op.get("inverses") or {})
        return net

    def _referrers_lost(self, inverses: dict[int, Any]) -> None:
        """The instances that referenced a removed one: IfcOpenShell edits each (its reference nulled or dropped)."""
        for step_id in inverses:
            self.referenced.add(step_id)
            if step_id not in self.created and step_id not in self.removed:
                self.changed.add(step_id)

    # ---------------------------------------------------------------- what to check

    def _known(self, model: ifcopenshell.file | None) -> ifcopenshell.file:
        if model is None:
            raise Unknown(_say_unknown("no model is open"))
        if self.why is not None:
            raise Unknown(_say_unknown(self.why))
        if model is not self.model:
            raise Unknown(_say_unknown("the session's model is not the one opened when the record began"))
        return model

    def _parts(self, model: ifcopenshell.file) -> tuple[dict[int, str], dict[int, ifcopenshell.entity_instance]]:
        """Each instance to check, with how it is in the edits, and the instances themselves."""
        how: dict[int, str] = {}
        insts: dict[int, ifcopenshell.entity_instance] = {}
        for kind, ids in (("created", self.created), ("changed", self.changed)):
            for step_id in ids:
                inst = _alive(model, step_id)
                if inst is not None:
                    how[step_id], insts[step_id] = kind, inst
        for step_id in list(how):
            inst = insts[step_id]
            for other in (*model.traverse(inst, max_levels=1), *model.get_inverse(inst)):
                other_id = other.id()
                if other_id and other_id not in how:
                    how[other_id], insts[other_id] = "neighbour", other
        for step_id in self.referenced:
            if step_id not in how:
                inst = _alive(model, step_id)
                if inst is not None:
                    how[step_id], insts[step_id] = "neighbour", inst
        return how, insts

    def _counts(self, how: dict[int, str]) -> dict[str, int]:
        counts = {"created": 0, "changed": 0, "removed": len(self.removed), "neighbours": 0}
        for kind in how.values():
            counts["neighbours" if kind == "neighbour" else kind] += 1
        return counts

    def scope(self, model: ifcopenshell.file | None) -> Scope:
        """What ifc_validate's scope "edits" checks, or ``Unknown`` when the record cannot say."""
        model = self._known(model)
        how, _ = self._parts(model)
        return Scope(ids=sorted(how), counts=self._counts(how), since=dict(self.since))

    def listing(self, model: ifcopenshell.file | None, how: str = "", element_type: str = "", offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """The instances the edits touched (or their neighbours, or what scope "edits" checks), a page at a time."""
        from flo2_ifc import reading

        model = self._known(model)
        how = how.strip()
        if how and how not in HOW:
            raise ValueError(f"how is one of {', '.join(HOW)}, or left out for what the edits created, changed and removed; not {how!r}")
        offset, limit = reading._check_page(offset, limit)
        element_type = reading._class_check(model, element_type)
        kinds, insts = self._parts(model)
        wanted = {"": ("created", "changed", "removed"), "checked": ("created", "changed", "neighbour")}.get(how, (how,))
        items: list[dict[str, Any]] = []
        for step_id in sorted(kinds):
            if kinds[step_id] in wanted and (not element_type or insts[step_id].is_a(element_type)):
                items.append({**element_of(insts[step_id]), "how": kinds[step_id]})
        if "removed" in wanted:
            schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(model.schema_identifier)
            for step_id in sorted(self.removed):
                gone = self.removed[step_id]
                if not element_type or _is_subclass(schema, gone["type"], element_type):
                    items.append({**gone, "how": "removed"})
            items.sort(key=lambda e: e["id"])
        page, info = reading.page_of(items, offset, limit)
        out: dict[str, Any] = {"since": dict(self.since), "counts": self._counts(kinds), "how": how or "edits", "elements": page, "page": info}
        more = reading._how_more("ifc_edited", info, " (the same how and element_type)")
        if more:
            out["more"] = more
        out["note"] = (
            "Since the model was opened (`since`): what the edits created, changed (edited, or left naming nothing by a"
            " removal) and removed, by step id; a removed one is named as it was. `neighbours` are what they reference"
            " and what references them, before and after the edits: ifc_validate with scope \"edits\" checks them with"
            " the edits' own (how \"checked\" lists that set)."
        )
        return out


def _is_subclass(schema: Any, ifc_class: str | None, of: str) -> bool:
    try:
        decl = schema.declaration_by_name(ifc_class)
    except Exception:  # noqa: BLE001 - not a class of this schema
        return False
    while decl is not None:
        if decl.name().lower() == of.lower():
            return True
        decl = decl.supertype()
    return False


class Step:
    """A change made whole or not at all: flo2-ifc's own steps (building.py, ``_hold_units``) run in one.

    With no transaction open (a step called alone, as tests and scripts call it) it is its own IfcOpenShell
    transaction, as before 0.12.0. Inside one already open (``Record.change``'s, while a tool's call is recorded) it is
    the part of that transaction from where it began: opening another would put the record's out of place, and what
    the step did after it would go unrecorded. ``take_back`` puts the model back as it was before the step either way.
    """

    def __init__(self, model: ifcopenshell.file):
        self.model = model
        self.outer = model.transaction
        if self.outer is None:
            model.begin_transaction()
        else:
            self.start = len(self.outer.operations)

    def keep(self) -> None:
        if self.outer is None:
            self.model.end_transaction()

    def take_back(self) -> None:
        if self.outer is None:
            self.model.end_transaction()
            self.model.undo()
            return
        part = Transaction(self.model)
        part.operations = list(self.outer.operations[self.start :])
        # Put back WITH the record's transaction open, so it sees the putting back as well (an instance the step made
        # is made and removed again; one it removed is put back with its own id): its count of instances made and
        # removed stays true, and what the step touched is still checked (more, never less).
        part.rollback()

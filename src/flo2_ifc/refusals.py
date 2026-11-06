"""A refusal says what was wrong and gives the form to copy (since 0.13.0).

A chat calls a tool with what it guesses the tool takes, and when the guess is wrong the refusal is the one text it is
sure to read at that moment: a chat that never read the skill or the tool's description still reads the refusal. So a
refusal names what was wrong in the tool's own terms, and gives the corrected call, built from the chat's own input,
first (flo2's door passes on a refusal's first 600 characters), then the forms the thing takes.

Until 0.13.0 every refusal flo2-ifc gave for a malformed call was the text of the library underneath, which the one
catch (server._reason_kept, since 0.4.0 the reason itself rather than the SDK's bare "Error executing tool") passes on:
lark's parser for a selector (``No terminal matches '~' ... Expected one of: * DOT``, the DOT because a lowercase
``name`` reads as a property set's name), pydantic's validator for a missing or mistyped argument, Python's importer
for an editing module that does not exist, ifcedit's ``Unknown parameter 'product' for spatial.assign_container``.
And an argument a tool does not take was dropped, not refused. Measured on flo2.io, 2026-11-02 to 11-05 (flo2-ifc's
design 1510fc3728665dbf, fact:a-chat-guessed-ifc-select-and-tool-argument-syntax-and-was-refused-repeatedly-2026-11-05,
and its root cause, fact:root-cause-a-refusal-passes-on-the-library-text-and-never-names-the-form-to-copy-2026-11-06):
one chat's six selector refusals, and the same ``~`` guessed again an hour after its refusal.

Three kinds of guess, each checked in one place:

- a SELECTOR (``selector_problem``): every tool's ``selector`` argument before the tool runs (server.Server), and
  every selector flo2-ifc's own readers resolve (``reading.selected``: ifc_draw's ``highlight``, ``around`` and
  ``through`` too). Refused: what IfcOpenShell cannot read, and two things it reads and answers with nothing, a
  value in single quotes and a class the model's schema does not have;
- an ARGUMENT (``arguments_problem``, ``validation_problem``): a name the tool does not take, a required one left
  out, a value of the wrong kind;
- an EDITING FUNCTION (``function_path_problem``, ``module_problem``, ``edit_error``): a path that is not
  ``<module>.<function>``, a module or a function that does not exist, a parameter the function does not take.

The text is flo2-ifc's; what is checked is IfcOpenShell's own: the selector's grammar, the schema's classes, the
editing functions' signatures. Nothing here decides what a call means: a call that reads is answered as before.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

#: The selector's own words that stay lowercase: everything else before a comparison is an attribute, capitalised.
SELECTOR_KEYWORDS = ("type", "material", "location", "classification", "group", "parent")
#: A lowercase attribute or place word a chat writes, and what the selector calls it.
ATTRIBUTES = {
    "name": "Name",
    "description": "Description",
    "objecttype": "ObjectType",
    "object_type": "ObjectType",
    "tag": "Tag",
    "longname": "LongName",
    "long_name": "LongName",
    "predefinedtype": "PredefinedType",
    "predefined_type": "PredefinedType",
    "globalid": "GlobalId",
    "global_id": "GlobalId",
    "guid": "GlobalId",
    "elevation": "Elevation",
    "storey": "location",
    "story": "location",
    "level": "location",
    "floor": "location",
}
#: The schemas a class is looked for in when the model's own lacks it.
SCHEMAS = ("IFC2X3", "IFC4", "IFC4X3")
#: Classes a chat names that IFC does not have, and the class it means.
CLASS_WORDS = {
    "IfcRoom": "IfcSpace",
    "IfcLevel": "IfcBuildingStorey",
    "IfcStorey": "IfcBuildingStorey",
    "IfcStory": "IfcBuildingStorey",
    "IfcFloor": "IfcSlab",
    "IfcFoundation": "IfcFooting",
    "IfcPipe": "IfcPipeSegment",
    "IfcDuct": "IfcDuctSegment",
}
#: How a selector is written, and forms to copy, each IfcOpenShell's own grammar (ifcopenshell.util.selector). Every
#: example is run by tests/test_refusals.py.
SELECTOR_FORMS = (
    "A selector is IFC classes and filters, separated by commas; + joins two selections. A filter is an attribute "
    "capitalised as IFC names it (Name, Description, ObjectType, Tag), or type, material, location or "
    "<Pset>.<Property>; then = (is), *= (contains, case-sensitive), != or !*= (is not, does not contain), >, <, >= or "
    "<=; then \"double-quoted text\", /a regex/ (matched from the start: /.*102/), a number, TRUE, FALSE or NULL."
)
SELECTOR_EXAMPLES = (
    ('IfcSpace, Name *= "102"', "rooms whose name contains 102"),
    ("IfcSpace, Name = /.*102.*/", "the same, by a regex"),
    ("IfcFooting + IfcPile + IfcSlab", "any of the three"),
    ('IfcWall, IfcDoor, location = "Level 1"', "the walls and doors on one storey"),
    ("IfcElement, ! IfcWall", "every element but the walls"),
    ("IfcWall, Pset_WallCommon.IsExternal = TRUE", "by a property"),
)


class Refused(ValueError):
    """A malformed call, refused before anything ran, with what was wrong and the form to copy."""


def _schema(name: str | None) -> Any:
    import ifcopenshell.ifcopenshell_wrapper as wrapper

    try:
        return wrapper.schema_by_name(name or "IFC4")
    except Exception:  # noqa: BLE001 - a schema IfcOpenShell does not know: names are then left as they are
        return None


def _class_name(word: str, schema: Any) -> str | None:
    """`word` as the schema spells its class (`ifcwall` is IfcWall), or None when it has none of that name."""
    if schema is None:
        return None
    try:
        return schema.declaration_by_name(word).name()
    except Exception:  # noqa: BLE001 - not a declaration of this schema
        return None


# ------------------------------------------------------------------ selectors

#: Quoted text, a regex, or single-quoted text: the parts of a selector its rewriting never touches inside.
_LITERAL = re.compile(r'("(?:[^"\\]|\\.)*"|/[^/]+/|\'[^\']*\')')
_REWRITES = (
    (re.compile(r"=="), "=", "One = compares: Name = \"W1\"."),
    (re.compile(r"=~|~=|~|\b(?:like|contains|includes|matches)\b", re.I), " *= ", "\"Contains\" is *= (case-sensitive); a pattern is a regex: Name = /.*102/."),
    (re.compile(r"\|\||\||\bor\b", re.I), " + ", "\"Or\" is + between two selections: IfcFooting + IfcPile."),
    (re.compile(r"&&|&|\band\b", re.I), ", ", "\"And\" is a comma: each filter after one narrows the selection."),
    (re.compile(r"\bnot\b", re.I), " ! ", "\"Not\" is !: IfcElement, ! IfcWall."),
)
_COMPARISON = r"(?=\s*(?:!?\*?=|>=|<=|>|<))"


def _unquoted(text: str, fn) -> str:
    """`text` with `fn` applied to each part outside quotes and regexes."""
    return "".join(part if _LITERAL.fullmatch(part) else fn(part) for part in _LITERAL.split(text))


def rewritten(selector: str, schema: Any) -> tuple[str, list[str]]:
    """The selector a chat most likely meant, and why each change was made: the comparison, joining and negating
    words other query languages use, an attribute in lowercase, a class in the wrong case, text in single quotes."""
    hints: list[str] = []

    def words(part: str) -> str:
        for pattern, to, hint in _REWRITES:
            part, n = pattern.subn(to, part)
            if n and hint not in hints:
                hints.append(hint)

        def attribute(m: re.Match) -> str:
            word = m.group(2)
            to = ATTRIBUTES.get(word.lower(), word[:1].upper() + word[1:])
            if word in SELECTOR_KEYWORDS or to == word:
                return m.group(0)
            hint = 'A storey is location = "<its name>".' if to == "location" else f"An attribute is capitalised as IFC names it: {to}, not {word}."
            if hint not in hints:
                hints.append(hint)
            return m.group(1) + to

        part = re.sub(r"(^\s*|[,+!]\s*)([a-z][A-Za-z_]*)" + _COMPARISON, attribute, part)

        def ifc_class(m: re.Match) -> str:
            word = m.group(0)
            to = _class_name(word, schema)
            if to and to != word:
                hint = f"A class is written as IFC names it: {to}."
                if hint not in hints:
                    hints.append(hint)
                return to
            return word

        part = re.sub(r"\b[Ii][Ff][Cc][A-Za-z0-9_]*", ifc_class, part)
        part = re.sub(r"\s*,\s*", ", ", part)
        part = re.sub(r"\s*\+\s*", " + ", part)
        part = re.sub(r"\s*(!?\*=|!=|>=|<=|=|>|<)\s*", r" \1 ", part)
        part = re.sub(r"!\s*(?=Ifc)", "! ", part)
        return re.sub(r"\s+", " ", part)

    out = _unquoted(selector, words)
    if re.search(r"'[^']*'", out):
        out = re.sub(r"'([^']*)'", lambda m: json.dumps(m.group(1)), out)
        hints.append("Text goes in double quotes.")
    return out.strip(), hints


def _parses(selector: str) -> Any:
    import ifcopenshell.util.selector as selectors

    return selectors.filter_elements_grammar.parse(selector)


def _unknown_classes(tree: Any, schema: Any) -> list[str]:
    if schema is None or tree is None:
        return []
    named = [str(t.children[0]) for t in tree.find_data("ifc_class")]
    return [c for c in dict.fromkeys(named) if _class_name(c, schema) is None]


def _single_quoted(tree: Any) -> list[str]:
    return [str(t.children[0]) for t in tree.find_data("unquoted_string") if re.fullmatch(r"'.*'", str(t.children[0]))]


def _where(error: Exception, selector: str) -> str:
    """Where the parser stopped, in a chat's terms."""
    import lark

    if isinstance(error, lark.exceptions.UnexpectedEOF) or getattr(error, "column", -1) in (-1, None):
        return "it ends where more was expected"
    column = getattr(error, "column", None)
    token = getattr(error, "token", None)
    seen = str(token) if token is not None else getattr(error, "char", None) or selector[column - 1 : column]
    return f"`{seen}` at character {column} is not part of one"


def _forms() -> str:
    return SELECTOR_FORMS + " For example: " + "; ".join(f"`{s}` ({what})" for s, what in SELECTOR_EXAMPLES) + "."


def selector_problem(selector: str, schema_name: str | None = None) -> str | None:
    """Why `selector` cannot be answered as meant, with the selector meant and how one is written; None when it reads.

    Refused: a selector IfcOpenShell's grammar cannot read, and two it reads and answers with nothing: a value in
    single quotes (the quotes are read as part of the text) and a class the model's schema does not have. `schema_name`
    is the model's (IFC2X3, IFC4, IFC4X3); without one, IFC4's classes are used to spell a class, and none is refused
    for its name."""
    import lark

    text = (selector or "").strip()
    if not text:
        return None
    schema = _schema(schema_name)
    #: The schema a class is refused against: the model's, only when one is open.
    known = schema if schema_name else None
    try:
        tree = _parses(text)
    except lark.exceptions.UnexpectedInput as e:
        what = f"`{text}` is not a selector IfcOpenShell reads: {_where(e, text)}."
    else:
        unknown = _unknown_classes(tree, known)
        quoted = _single_quoted(tree)
        if not unknown and not quoted:
            return None
        if unknown:
            what = f"`{text}` names {', '.join(unknown)}, which {'is not a class' if len(unknown) == 1 else 'are not classes'} in {schema_name}, the model's schema, so it would select nothing."
        else:
            what = f"`{text}` gives {', '.join(quoted)} in single quotes, which a selector reads as part of the text, so it would match nothing."
    meant, hints = rewritten(text, schema)
    for c in _unknown_classes(_parses_or_none(meant), known):
        elsewhere = [s for s in SCHEMAS if s != schema_name and _class_name(c, _schema(s))]
        if elsewhere:
            hints.append(f"{c} is a class of {' and '.join(elsewhere)}, not of {schema_name}.")
            continue
        to = CLASS_WORDS.get(c)
        if to:
            hints.append(f"IFC has no {c}; {to} is the class for it.")
        else:
            to = next(iter(difflib.get_close_matches(c, _classes(schema), n=1, cutoff=0.85)), None)
            if to:
                hints.append(f"IFC has no {c}; the nearest name is {to}.")
        if to:
            meant = re.sub(rf"\b{re.escape(c)}\b", to, meant)
    tree = _parses_or_none(meant)
    good = meant != text and tree is not None and not _unknown_classes(tree, known) and not _single_quoted(tree)
    parts = [what]
    if good:
        parts.append(f"Did you mean `{meant}`?")
    parts += hints
    parts.append(_forms())
    return " ".join(parts)


def _parses_or_none(selector: str) -> Any:
    import lark

    try:
        return _parses(selector)
    except lark.exceptions.UnexpectedInput:
        return None


def _classes(schema: Any) -> list[str]:
    if schema is None:
        return []
    return [d.name() for d in schema.declarations() if d.name().startswith("Ifc")]


# ------------------------------------------------------------------ arguments

#: A name a chat gives an argument, and the name a tool takes for it (server.ARGUMENT_NAMES, and ifcmcp's own).
GUESSED_NAMES = {
    "where": "selector",
    "filter": "selector",
    "select": "selector",
    "query": "selector",
    "type": "element_type",
    "class": "element_type",
    "ifc_class": "element_type",
    "class_name": "element_type",
    "function": "function_path",
    "func": "function_path",
    "path": "function_path",
    "args": "params",
    "arguments": "params",
    "kwargs": "params",
    "parameters": "params",
    "id": "element_id",
    "guid": "element_id",
    "global_id": "element_id",
    "ids": "element_ids",
}
#: Names that say a storey, for a tool that takes a selector and no `storey`: the storey goes in the selector.
PLACE_NAMES = ("location", "storey", "story", "level", "floor")
#: Tools whose own function's arguments go inside `params`, and how each value is written there: ifcedit converts
#: each from TEXT by the function's type hints (a list as "[86]", an element as "86"); the shape builder takes them as
#: themselves (flo2's door, model-door.ts, editParamsAsText and shapeParamsAsJson, measured both ways).
PARAMS_AS_TEXT = {"ifc_edit": True, "ifc_shape": False}


def _param_says(description: str | None, name: str) -> str:
    """What a tool's description says of one argument (its ``:param name:`` text), to its first sentence."""
    m = re.search(rf":param {re.escape(name)}:\s*(.*?)(?=\n\s*:|\n\s*\n|\Z)", description or "", re.S)
    if not m:
        return ""
    said = " ".join(m.group(1).split())
    end = re.search(r"(?<=[a-z0-9)\"'`])\.(\s|$)", said)
    return said[: end.start() + 1] if end else said


def _takes(schema: dict[str, Any]) -> str:
    props = list(schema.get("properties", {}))
    required = set(schema.get("required", []))
    if not props:
        return "It takes no arguments."
    return "It takes " + ", ".join(f"{p} (required)" if p in required else p for p in props) + "."


def _as_text(value: Any) -> str:
    if value is None:
        return "None"
    return value if isinstance(value, str) else json.dumps(value)


def arguments_problem(tool: str, schema: dict[str, Any], description: str | None, arguments: dict[str, Any]) -> str | None:
    """Why `arguments` are refused before `tool` runs, with the call meant; None when every name is one it takes.

    A name the tool does not take is refused, never dropped: a caller who sent it meant something by it, and a call
    run without it does something else (ifc_edit with its function's arguments beside `params` made an element of
    the wrong class with no name). The call meant: each name renamed to the one the tool takes (``GUESSED_NAMES``, or
    the closest by spelling), a storey moved into the selector, a function's own arguments moved into ``params``."""
    props = schema.get("properties", {})
    unknown = [k for k in arguments if k not in props]
    if not unknown:
        return None
    meant: dict[str, Any] = {}
    folded: dict[str, Any] = {}
    renamed: dict[str, str] = {}
    placed: set[str] = set()
    hints: list[str] = []
    for k, v in arguments.items():
        if k in props:
            meant[k] = v
            continue
        to = GUESSED_NAMES.get(k.lower())
        if k.lower() in PLACE_NAMES and "storey" in props:
            to = "storey"
        to = to if to in props and to not in arguments else None
        if to is None and k.lower() in PLACE_NAMES and "selector" in props and isinstance(v, str):
            base = str(arguments.get("selector") or "IfcElement").strip()
            meant["selector"] = f"{base}, location = {json.dumps(v)}"
            placed.add(k)
            hints.append(f"A storey goes in the selector: location = {json.dumps(v)}.")
            continue
        if to is None:
            close = difflib.get_close_matches(k, [p for p in props if p not in arguments], n=1, cutoff=0.6)
            to = close[0] if close else None
        if to is None and tool in PARAMS_AS_TEXT and "params" in props:
            folded[k] = v
            continue
        if to is None:
            continue
        meant[to] = v
        renamed[k] = to
    if folded:
        given = arguments.get("params") or {}
        if isinstance(given, str):
            try:
                given = json.loads(given) if given.strip() else {}
            except json.JSONDecodeError:
                given = None
        if isinstance(given, dict):
            inside = {**given, **folded}
            if PARAMS_AS_TEXT[tool]:
                inside = {k: _as_text(v) for k, v in inside.items()}
            meant["params"] = json.dumps(inside)
            whose = "The function's" if tool == "ifc_edit" else "The method's"
            hints.append(f"{whose} own arguments go inside `params`" + (", each value as text." if PARAMS_AS_TEXT[tool] else "."))
    resolved = set(renamed) | set(folded) | placed
    parts = [f"{tool} does not take {', '.join(f'`{k}`' for k in unknown)}."]
    if resolved >= set(unknown) and all(r in meant for r in schema.get("required", [])):
        parts.append(f"Call it as: `{json.dumps(meant)}`.")
    for k, to in renamed.items():
        says = _param_says(description, to)
        parts.append(f"`{k}` is `{to}`" + (f": {says}" if says else "."))
    parts += hints
    parts.append(_takes(schema))
    return " ".join(parts)


_KINDS = {"integer": "a whole number", "number": "a number", "string": "text", "boolean": "true or false", "array": "a list", "object": "an object of named values", "null": "null"}


def _kind(prop: dict[str, Any]) -> str:
    kinds = [prop["type"]] if "type" in prop else [b.get("type") for b in prop.get("anyOf", []) if b.get("type") != "null"]
    return " or ".join(_KINDS.get(str(k), str(k)) for k in kinds if k) or "a value"


def validation_problem(tool: str, schema: dict[str, Any], description: str | None, errors: list[dict[str, Any]]) -> str:
    """The MCP SDK's argument check (pydantic's errors) in the tool's own words: what each argument is, and what
    was given."""
    props = schema.get("properties", {})
    parts: list[str] = []
    for e in errors:
        name = str(e.get("loc", ["?"])[0])
        says = _param_says(description, name)
        if e.get("type") == "missing":
            parts.append(f"{tool} needs `{name}`" + (f": {says}" if says else "."))
        else:
            given = json.dumps(e.get("input"), default=str)
            parts.append(f"`{name}` is {_kind(props.get(name, {}))}, not {given[:80]}" + (f": {says}" if says else "."))
    parts.append(_takes(schema))
    return " ".join(parts)


# ------------------------------------------------------------------ editing functions


def _api_modules() -> list[str]:
    import ifcopenshell.api

    return sorted(p.name for p in Path(ifcopenshell.api.__path__[0]).iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "__init__.py").exists())


def _functions(module: str) -> list[dict[str, Any]]:
    from ifcedit.discover import list_functions

    return list_functions(module)


#: What a chat calls an editing module, and the module that does it.
MODULE_WORDS = {
    "space": "spatial",
    "room": "spatial",
    "storey": "spatial",
    "container": "spatial",
    "property": "pset",
    "properties": "pset",
    "quantity": "pset",
    "qto": "pset",
    "entity": "root",
    "element": "root",
    "create": "root",
    "attributes": "attribute",
    "placement": "geometry",
    "shape": "geometry",
    "opening": "feature",
    "void": "feature",
}
#: Modules a chat names that are a building step instead (building.py): one call makes the whole part.
BUILT_IN_ONE_CALL = {"space": "ifc_add_space", "room": "ifc_add_space", "wall": "ifc_add_wall", "slab": "ifc_add_slab", "floor": "ifc_add_slab", "roof": "ifc_add_roof"}


def module_problem(module: str) -> str | None:
    """Why `module` is not an editing module, with the ones there are; None when it is one."""
    modules = _api_modules()
    if module in modules:
        return None
    word = MODULE_WORDS.get(module.lower())
    close = [word] if word in modules else difflib.get_close_matches(module, modules, n=2, cutoff=0.6)
    parts = [f"There is no editing module `{module}`."]
    if close:
        parts.append("Did you mean " + " or ".join(f"`{c}`" for c in close) + "?")
    step = BUILT_IN_ONE_CALL.get(module.lower())
    if step:
        parts.append(f"{step} builds one in one call (made, shaped, placed and on its storey).")
    parts.append("The modules: " + ", ".join(modules) + ".")
    return " ".join(parts)


def function_path_problem(function_path: Any) -> str | None:
    """Why `function_path` is not ``<module>.<function>``, with the functions of the module it names; None when it
    is of that shape. A module or a function that does not exist is said by the tool itself: ifc_docs as ifcmcp says
    it ("Function '<path>' not found", kept as 0.3.1 gave it: tests/test_tool_list.py), ifc_edit by ``edit_error``."""
    path = str(function_path or "").strip()
    module, dot, function = path.partition(".")
    if dot and module and function:
        return None
    if path in _api_modules():
        names = [f["name"] for f in _functions(path)]
        return (
            f"`{path}` is a module, and `function_path` is `<module>.<function>`, like "
            + ", ".join(f"`{path}.{n}`" for n in names[:3])
            + f". Its functions: {', '.join(names)}."
        )
    return f"`{path}` is not `<module>.<function>`: `function_path` names one editing function, like `spatial.assign_container` or `attribute.edit_attributes` (ifc_list names them)."


def _close_function(module: str, function: str) -> str:
    names = [f["name"] for f in _functions(module)]
    close = difflib.get_close_matches(function, names, n=2, cutoff=0.6)
    said = ("Did you mean " + " or ".join(f"`{module}.{c}`" for c in close) + "? ") if close else ""
    return said + f"{module}'s functions: {', '.join(names)}."


def _signature(module: str, function: str) -> tuple[list[dict[str, Any]], str]:
    spec = next((f for f in _functions(module) if f["name"] == function), None)
    params = (spec or {}).get("params", [])
    return params, ", ".join(f"`{p['name']}` ({'required, ' if p.get('required') else ''}{p.get('type', '?')})" for p in params)


def edit_error(function_path: str, error: str) -> str:
    """ifcedit's reason a function did not run, with what the function takes: the parameters by name and type, and
    the one meant when a name is close to one of them. Any other reason is left as it is."""
    module, _, function = str(function_path).partition(".")
    try:
        unknown = re.match(r"Unknown parameter '([^']+)' for ", error)
        if unknown:
            params, said = _signature(module, function)
            names = [p["name"] for p in params]
            close = difflib.get_close_matches(unknown.group(1), names, n=1, cutoff=0.6)
            meant = f" Did you mean `{close[0]}`?" if close else ""
            return f"{error}.{meant} {function_path} takes {said}; ifc_docs {json.dumps(function_path)} says each one."
        if error.startswith("Cannot find function "):
            if module not in _api_modules():
                return f"{error.split(':', 1)[0]}. " + (module_problem(module) or "")
            return f"{error.split(':', 1)[0]}. " + _close_function(module, function)
    except Exception:  # noqa: BLE001 - the reason as ifcedit gave it, rather than none
        return error
    return error

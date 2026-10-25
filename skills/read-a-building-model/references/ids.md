# What the model must contain: an IDS

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

An IDS (buildingSMART's Information Delivery Specification) is a list of what a model must contain, in a form a
program checks: "every room has a name and a net floor area", "every wall says its fire rating", "every door is on a
storey". It is how a person says what they need from a model, and how a builder or an architect is asked for it.

1. **Write the list in their words first.** Ask what must be in the model and why. Each item becomes one
   specification: what it APPLIES TO (usually one kind of element: `{"facet": "entity", "name": "IfcSpace"}`) and
   what those must HAVE (a name, a property in a property set, a quantity, a material, a classification, or being on a
   storey). Read the list back to them before you write it.
2. **Write it with `write_ids`** (`ifc_write_ids` on the server), never as XML by hand. For example, every room has a
   name and a net floor area:

   ```json
   {"title": "Shop must-haves", "specifications": [
     {"name": "Every room has a name and a net floor area",
      "applicability": [{"facet": "entity", "name": "IfcSpace"}],
      "requirements": [{"facet": "attribute", "name": "Name"},
                       {"facet": "property", "property_set": "Qto_SpaceBaseQuantities", "property": "NetFloorArea"}]},
     {"name": "Every wall says its fire rating",
      "applicability": [{"facet": "entity", "name": "IfcWall"}],
      "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating"}]}]}
   ```

   A value can be required exactly (`"value": "EI60"`), from a list (`{"one_of": ["EI60", "EI90"]}`), by a pattern
   (`{"pattern": "[A-Z][0-9]{3}"}`) or within bounds (`{"at_least": 10}`). **Units:** a property's or quantity's
   value is in SI units (metres, square metres), as IDS 1.0 says, whatever the model's unit, because the checker
   converts; an attribute's value (a door's `OverallWidth`) is compared as stored, in the model's own length unit
   (millimetres on a Revit export). A 12-foot door is `{"at_least": 3657}` as an attribute on a millimetre model. A specification is `required` (the default: the model must have at least one such element),
   `optional`, or `prohibited` (no such element may exist). An invalid list is refused with the reason, which names
   the specification and the facet: correct that part and write it again.
3. **Know what an IDS cannot say.** It checks what the model STORES (classes, attributes, properties, quantities,
   materials, classifications, where an element sits), not its shapes. "The shop is 40 by 60 feet" or "the roof is
   under 30 feet" is a measure (`ifc_footprint`, `ifc_extent`), unless the model stores that number as a property.
   Say which items went into the IDS and which stay measures, and why.
4. **Check the model against it with `check_ids`** (the IDS named by its file name; the only one when the design
   keeps one). Each specification comes back `passed` or `failed`, with `applicable` (how many elements it applies
   to) and the failing elements by GlobalId with the checker's reason, a page at a time (`offset`). A required
   specification that applies to nothing fails too ("no element is one it applies to"). The checker's own HTML report
   is kept in the design for the person, with a JSON one beside it: give them the HTML report's link.
5. **Say it plainly:** how many specifications passed, which failed, and for each failure what is missing and on
   how many elements ("all 4 walls lack Pset_WallCommon.FireRating"). Change nothing in the model unless they ask.

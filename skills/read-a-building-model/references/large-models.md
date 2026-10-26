# A large model

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

A real building (a large model holds well over a hundred thousand entities, and hundreds of walls) does not fit in
one answer, and nothing should be read whole:

- `ifc_tree`, `ifc_select` and `ifc_relations` list a page at a time (100 by default). Each answer says how many it
  withheld and the `offset` for the next page. Narrow before you page: `ifc_tree` with `storey` and
  `element_type`; `ifc_relations` with `element_type`; `ifc_select` with `location`.
- Counts come first: `ifc_summary` and `ifc_tree`'s `element_counts` and `ifc_select`'s `counts` answer "how many"
  without listing.
- An element that spans storeys, such as a wall rising through two, is contained in one storey and referenced into
  the others. `ifc_tree` lists it under each, marked `"referenced": true` and counted in `referenced_counts`;
  `ifc_info` names the others (`referenced_into`); a plan of each storey cuts it. `location = "<storey>"` in a
  selector picks only what the storey contains.
- `ifc_validate` checks for about 30 s a call. When the answer says `complete: false`, call again with `start` set
  to its `next_start`; only `complete: true` with `valid: true` means the whole model passed. The rules
  (`express_rules`) may not finish in one call on a model this size, and the answer says so.
- `ifc_extent` on the whole model takes about 25 s; on a kind of element or one storey, a moment. If it says
  `partial`, narrow the selector.

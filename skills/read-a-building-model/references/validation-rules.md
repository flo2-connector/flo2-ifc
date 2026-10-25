# buildingSMART's validation rules

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

Before a model is handed over, check it is sound, in two steps:

- `ifc_validate` checks it against the IFC schema (and with `"express_rules": true`, the schema's own rules);
- `ifc_check_rules` runs buildingSMART's validation rules beyond the schema, the normative ones its validation service
  runs (implementer agreements, informal propositions), plus industry-practice rules that only warn. Each rule says
  `passed`, `error`, `warning` or `not_applicable`; each issue names the element by id and GlobalId, with what was
  expected and what was found. `"rule_sets": "normative"` runs only the normative ones.

On a large model the rules take several calls: call again with `start` set to `next_start` until `complete` is true.
A rule too long for one call is named in `rules_not_finished` and was NOT checked: say so. The answer also says what
did not run here (the bSDD check needs the network) and flags rules with a caveat (`rules_with_a_caveat`): read an
error or a not_applicable from those with care. Report errors first, then warnings, in the person's words, with the
element's name. The rules check the FILE: save an edited copy with `save_model_as` and check the saved file.

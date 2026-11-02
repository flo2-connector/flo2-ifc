# Record what changed between two models: IfcDiff and BCF

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

When a person has changed a model (a copy kept with `save_model_as`, or a new version they upload) and wants the
change written down in openBIM's own form, record it in two files the design keeps: what changed, element by element
(IfcOpenShell's IfcDiff), and one BCF topic per change they MEANT, pinned to its elements, with their reason. Any BCF
tool (Bonsai, Solibri, BIMcollab, Revit's BCF add-ins) opens the BCF file.

1. **Compare the model before with the model after: `ifc_diff`.** Open the model BEFORE (`file`) and name the one
   AFTER (in flo2 the step `ifc_diff` with `other`, the after model's file name; `other_is` "before" when the open one
   is the later). It answers every element added, deleted and changed, each by GlobalId, IFC class and name, and what
   changed on each: which attributes, with the value before and after (a renamed wall), its properties (IfcDiff's
   record of which property sets and values changed), its type, its storey (container), what it is part of, its
   classifications, and its geometry, with `placement.moved_by` when it moved (in the model's length unit). The whole
   comparison is kept as a NEW JSON file in the design; the reply lists 50 of each list at a time (`offset`, `limit`)
   and says what it withheld, so read the rest a page at a time or point the person to the file.
   - Elements are matched by GlobalId. A model exported again from Revit with new GlobalIds reads as everything
     deleted and added: say so rather than report it as a change.
   - It compares everything IfcDiff can by default. On a large model, compare less if it is refused for time:
     `compare` "attributes, property, type, container" leaves the geometry (the slowest part) out, and `selector`
     (e.g. `"IfcWall, IfcDoor"`) compares only those elements. Two models too large for the helper's memory are
     refused before either is opened: compare part of them with `selector`.
2. **Sort what IfcDiff found into the changes the person meant.** A change they asked for ("move the kitchen wall
   600 mm east") usually touches several elements (the wall, the door in it, the room beside it); one edit can also
   show as several kinds of change on one element. Read the changed elements back to them in their words, group them
   into the changes they meant, and ASK their reason for each, in their words. Something IfcDiff found that no change
   of theirs explains (an element they did not touch) is worth asking about, not hiding.
3. **Write the record: `write_bcf`** (`ifc_write_bcf` on the server), never BCF XML by hand. One topic per change
   they meant:

   ```json
   {"project": "Shop", "topics": [
     {"title": "Kitchen wall moved 600 mm east",
      "description": "Moved so the fridge fits. The owner's reason, in their words: ...",
      "topic_type": "Request", "topic_status": "Open", "author": "<their name or email>",
      "labels": ["architecture"],
      "components": ["2O2Fr$t4X7Zf8NOew3FNr2", "2O2Fr$t4X7Zf8NOew3FNtn"],
      "colour": "orange", "files": ["shop.ifc", "shop-edited.ifc"],
      "comments": [{"author": "<their name>", "comment": "Checked against the fridge's spec."}]}]}
   ```

   - `components` are the GlobalIds `ifc_diff` gave, so the topic's viewpoint selects exactly those elements; a
     deleted element's GlobalId is only in the model before.
   - `description` carries what changed and WHY, in the person's words; `topic_type` and `topic_status` are the
     words their team uses (for example Request and Open; the file lists them for other tools).
   - In flo2, `snapshot` names a picture the design keeps (a `render_model` PNG, ideally with the change picked out
     with `highlight`) to show with the topic; `files` names the model files it is about, as the design keeps them;
     `camera` is optional (in metres): without one, it looks at the topic's elements in the open model.
   - At most 100 topics a file, 2,000 elements a topic, 50 comments a topic. A refused description says which topic
     and what is wrong: correct that and write it again.
   - The file is checked against buildingSMART's BCF 3.0 schemas and read back before it is kept as a NEW `.bcf` file
     in the design.
4. **Tell the person** what the record holds (each topic's title and how many elements it pins) and give them both
   files' links. Change nothing in the model.

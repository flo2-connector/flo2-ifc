# Changelog

flo2-ifc's versions, newest first. Versions before 0.3.1 are described in README.md, in the sections marked "since".

## 0.3.1 (2026-10-24)

- **A room that holds other rooms is named clear of them, or not at all.** `ifc_draw` drew such a room's name at its
  middle, on top of the names of the rooms inside it, so neither could be read. Now it is named in its part clear of
  the rooms it holds when its name fits there whole, and otherwise left unnamed on the sheet, with the reply listing it
  in `unlabelled_rooms` and saying so in `notes`. "Holds" is most of the other room, not all of it: at least 90% of
  its drawn area inside, and the other the smaller. The names of rooms that hold none do not move. Sections name rooms
  the same way.
- **A freed model's meshes go with it.** The meshes a session keeps were keyed by `id(model)`, which Python gives to
  the next object made once a model is freed, so a model made after another was freed could be drawn with the first
  one's shapes (seen in this release's tests: 8 of 30 pairs in a loop). The server already forgot every mesh when its
  model changed; now a model's meshes are also dropped when the model itself is freed.
- Example names and figures in the README, the tool descriptions, the code's comments and the tests are made generic
  (storeys such as "Level 1", "a large building model").
- The build backend's pin moves from setuptools 84.0.0 to 84.2.1, its latest release in the same major series. The
  package builds and its tests pass as before.

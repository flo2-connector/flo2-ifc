"""A behave formatter that writes each feature's validation outcomes as one line, as the feature ends.

buildingSMART's rules, run from the console, hand each feature's outcomes to
every formatter that has an ``embedding`` method (their
``features/environment.py``, ``after_feature``), as JSON. Their own formatter
keeps them for one JSON document written when the run ends, so a run stopped
at a time limit would leave nothing. This one writes a line per feature at
once, prefixed ``FLO2-RULES ``, so flo2_ifc.rules keeps every feature that
finished. It formats nothing else.
"""

from __future__ import annotations

import json
import os

from behave.formatter.base import Formatter


class Lines(Formatter):
    name = "flo2-rules-lines"
    description = "Each feature's validation outcomes as one JSON line, as it ends."

    def __init__(self, stream_opener, config):
        super().__init__(stream_opener, config)
        self.stream = self.open()
        self._feature = None

    def feature(self, feature):
        self._feature = feature

    def embedding(self, mime_type, data, target="feature", attribute_name="embedded"):
        if attribute_name != "validation_outcomes" or self._feature is None:
            return
        try:
            outcomes = json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data)
        except (ValueError, UnicodeDecodeError):
            outcomes = []
        line = {"file": os.path.relpath(str(self._feature.filename)), "name": self._feature.name, "outcomes": outcomes}
        self.stream.write("FLO2-RULES " + json.dumps(line, default=str) + "\n")
        self.stream.flush()

    def close(self):
        self.close_stream()

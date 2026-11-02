# BCF 3.0 schemas (buildingSMART International)

These seven XML Schema files are buildingSMART International's BCF-XML 3.0 schemas, copied **unchanged** from
<https://github.com/buildingSMART/BCF-XML>, folder `Schemas`, at tag `v3.0` (commit
`5834466b2477ac700d7a61990b4e67ec1005cc68`). flo2-ifc's `ifc_write_bcf` checks every part of a BCF file it writes
against them before it keeps the file (`src/flo2_ifc/bcf_writing.py`), with no network.

| File | sha256 |
|---|---|
| `documents.xsd` | `105da6cd7452e6a7c9ea54a6f0ac2f0420e7d8c6fb8d2ac9f6f2075edae4178f` |
| `extensions.xsd` | `df9b49fc2368aba41cec8259fad266fe82d735b5319469834e7fe19bf0d83153` |
| `markup.xsd` | `6c80f1c566ace8b405ae0ab530438aec2014bf35dd14cfc3c4cf0c5aa0b9d169` |
| `project.xsd` | `44cf05c63fddcba335589cbbf5824458118561ffc23bb6c3e72a1f3217cce68a` |
| `shared-types.xsd` | `93d20dfcc6d7f451ae85a8277559b0347478c278218a42f0ef41dcc5550a2090` |
| `version.xsd` | `cef0dcc112239457776cab33a5adeed0cf61c344e05bb5c6c4674c2df3643fa6` |
| `visinfo.xsd` | `0855bc002310b4a9decd67b1b21631bd2e0368f0878183da8448e34d807e7418` |

`tests/test_bcf.py` holds each file to its sha256 above, so a change to them is seen.

## Licence

(c) buildingSMART International Ltd. Licensed under the Creative Commons Attribution-NoDerivatives 4.0 International
License (<http://creativecommons.org/licenses/by-nd/4.0/>), as the BCF-XML repository's `LICENSE` says. They are
shared here verbatim, with this attribution, and are not modified: CC BY-ND 4.0 allows that, and allows no adapted
version. They are not covered by flo2-ifc's own Apache-2.0 licence.

# modelferry handoff (before Phase 5)

Temporary file. Delete in Phase 5.

## Status

Phases 1-4 complete. Six CI jobs green (lint, unit x3.10/3.11/3.12, integration,
airgap-e2e).

Commit map:

- `spec` - SPEC.md + CLAUDE.md
- `phase 1: scaffold`
- `phase 2: offline verifier + tests`
- `phase 2.2: verifier hardening` = **freeze commit `0b4cfc8`** (also tag `manifest-v1`)
- `phase 3: pack side + integration test`
- `phase 4: hardening`

## Invariants (do not break)

- `src/modelferry/offline.py` and its tests are **frozen** at `0b4cfc8`
  (byte-identical). Verify with `git diff --stat 0b4cfc8 -- src/modelferry/offline.py tests/test_offline_*.py tests/_bundle.py tests/conftest.py`.
- offline.py line cap is **550** (CLAUDE.md, SPEC section 7, lint test constant).
- `manifest_version` is **1**, format frozen. Any structural change bumps the
  version and updates SPEC section 5 in the same commit.
- Downloads use huggingface_hub **local_dir mode** (real files under `--staging`,
  no symlinks, resumable). This is what makes the network test pass on Windows
  without Developer Mode. Do not revert to cache_dir mode.
- Runtime dependencies are still exactly three: typer, rich, huggingface_hub.

## Next: Phase 5 (release), per SPEC section 13

- README: include the trust-model section from SPEC section 9 verbatim in spirit;
  leave the war-story intro as a `TODO` block for Hamza to write.
- CHANGELOG.
- `.github/workflows/release.yml` with PyPI trusted publishing (on tag).
- vhs tape for the terminal GIF.
- TestPyPI dry run before the real publish.
- Tag `v0.1.0`.
- Flip the repo public.

## Manual pre-announce checks (outside CI)

- Pack a ~15 GB repo (e.g. Qwen2.5-7B-Instruct), watch memory stay flat.
- One physical FAT32 USB round-trip.
- One 30-80 GB overnight pack.

## Open item

Owed to the external reviewer before release: the rendered `MANIFEST.md` plus the
`inspect` output from a local pack, for a wording review.

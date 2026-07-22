# modelferry

CLI that packs Hugging Face models into chunked, sha256-manifested bundles for transfer into air-gapped environments, and verifies/unpacks them offline. SPEC.md is the contract: read the relevant sections before implementing anything. If the spec turns out to be wrong or ambiguous, stop and ask; when we agree on a change, update SPEC.md in the same commit as the code.

## Commands

- Setup: `uv sync`
- Tests (default, no network): `uv run pytest -m "not network"`
- All tests: `uv run pytest`
- Lint + format: `uv run ruff check . && uv run ruff format .`
- Air-gap end-to-end (needs docker): `bash scripts/e2e_airgap.sh`

## Hard rules

- IMPORTANT: `src/modelferry/offline.py` uses the Python standard library only, runs on CPython 3.9, imports nothing from the modelferry package, and stays under ~550 lines (raised from 500 in phase 2.2 to fit symlink/atomic-join/part-name hardening). It is copied verbatim into every bundle. The AST test in tests/ enforces this; never loosen that test.
- IMPORTANT: never write tokens or secrets into bundles, manifests, receipts, or logs. `HF_TOKEN` is read from the environment only.
- manifest.json format is frozen once Phase 2 merges. Any structural or semantic change bumps `manifest_version` and updates SPEC.md §5 in the same commit.
- All payload IO is streamed with a fixed 8 MiB buffer. Never read a payload file fully into memory.
- Unpack rejects absolute paths, `..` segments, and anything resolving outside the destination. Never create symlinks.
- Exit codes exactly per SPEC.md §10. Do not invent new ones.
- Runtime dependencies are typer, rich, huggingface_hub, and pynacl (added in 0.2.0 for connected-side signing in signing.py; it must never enter offline.py). Do not add another without asking.
- Never weaken a corruption, path-safety, or token-leak test to make it pass. Fix the code instead.
- Every bugfix ships with a regression test.

## Workflow

- Build in the phase order of SPEC.md §13, one phase per session.
- Mark network-dependent tests `@pytest.mark.network` so the default test run stays offline.
- Before declaring a phase done: ruff clean, `pytest -m "not network"` green, and for Phase 4+ the e2e script passes.
- Commit at phase boundaries with short imperative messages (e.g. "phase 2: offline verifier + tests").

## Prose style (README, MANIFEST.md template, all docs)

- Plain sentences, contractions, no em dashes, no marketing adjectives, no bullet-point walls. Write like an engineer explaining to another engineer.
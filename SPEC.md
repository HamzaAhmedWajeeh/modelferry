# modelferry v1 specification

Status: draft for build. Working name `modelferry` (PyPI free as of 2026-07-15; rename is a find-replace).

This document is the contract. Code follows the spec. If implementation reveals the spec is wrong, stop, discuss, update the spec, then code.

## 1. Problem and goal

Moving LLM weights into air-gapped environments today means ad-hoc downloads, manual checksums, files split by hand to fit transfer media, and nothing a security officer can approve. modelferry packs a Hugging Face model into a chunked, hashed, self-verifying bundle on the connected side, and verifies and reassembles it on the disconnected side with zero network and zero third-party dependencies.

The manifest is the product. It doubles as the approval document for the client's security review.

## 2. v1 scope

In scope:

- Pack a single Hugging Face model repo (pinned to a commit) into a bundle.
- Verify a bundle offline.
- Unpack a bundle offline into a directory that vLLM / transformers can load directly.
- Inspect a bundle offline (print summary).
- Human-readable MANIFEST.md for security review.
- Chunking for FAT32 / limited transfer media.

Out of scope for v1 (see §14 parking lot): datasets, docker images, pip wheels, cryptographic signing, multi-volume spanning across several drives, Windows CI, encryption.

## 3. CLI surface

Installed tool (connected side), built with Typer:

```
modelferry pack REPO_ID [--revision REV] --dest DIR
    [--chunk-size 3900M | none]
    [--include GLOB ...] [--exclude GLOB ...]
    [--staging DIR]
modelferry verify BUNDLE_DIR [--quiet]
modelferry unpack BUNDLE_DIR DEST_DIR [--no-verify] [--force]
modelferry inspect BUNDLE_DIR
```

`verify`, `unpack`, and `inspect` in the installed CLI are thin wrappers around the same code that ships inside every bundle (§7).

Notes:

- `--revision` defaults to `main`. Pack resolves it to a commit SHA first and downloads at that SHA. The manifest records both.
- `--chunk-size` accepts `3900M`, `16G`, `none`. Default `3900M` (fits FAT32's 4 GiB file limit with margin).
- `--include` / `--exclude` are fnmatch patterns against repo-relative paths. Exclude wins. Typical use: `--exclude "*.bin"` when safetensors exist.
- Hugging Face auth: `HF_TOKEN` environment variable only. Never a flag, never written to any file (§9).
- Custom hub endpoints: respect the standard `HF_ENDPOINT` env var. Record the endpoint used in the manifest.
- `--staging` defaults to `~/.cache/modelferry/`. Re-running pack after an interruption resumes the download (huggingface_hub handles resume).

## 4. Bundle layout

```
<bundle-name>/                      e.g. qwen2.5-7b-instruct__1a2b3c4/
  manifest.json                     machine-readable manifest (§5)
  manifest.sha256                   hex sha256 of manifest.json bytes + filename, sha256sum format
  MANIFEST.md                       human-readable manifest for review (§6)
  tools/
    modelferry_offline.py           self-contained verifier/unpacker (§7), copied in at pack time
  payload/
    config.json                     small files stored as-is, repo-relative paths preserved
    model-00001-of-00004.safetensors.mfpart0000
    model-00001-of-00004.safetensors.mfpart0001
    subdir/big.safetensors.mfpart0000   parts sit next to their parent file
    ...
```

Bundle name: `<repo name slugified, lowercase>__<7-char commit sha>`.

Files larger than chunk size exist in `payload/` only as `.mfpartNNNN` parts (4-digit, zero-padded, ASCII sort order = join order). Files at or under chunk size are stored whole. Part files sit next to their parent file, mirroring the repo tree: a chunked file at `subdir/big.safetensors` has its parts at `payload/subdir/big.safetensors.mfpartNNNN`. The original file tree is reconstructed at unpack.

## 5. manifest.json schema

Serialized with `json.dumps(..., indent=2, sort_keys=True)` and a trailing newline, so output is deterministic and diffs cleanly.

```json
{
  "manifest_version": 1,
  "bundle_name": "qwen2.5-7b-instruct__1a2b3c4",
  "created_at": "2026-07-15T09:30:00Z",
  "tool": {
    "name": "modelferry",
    "version": "0.1.0",
    "python": "3.12.3",
    "platform": "Linux-6.8-x86_64"
  },
  "source": {
    "type": "huggingface",
    "endpoint": "https://huggingface.co",
    "repo_id": "Qwen/Qwen2.5-7B-Instruct",
    "repo_type": "model",
    "revision_requested": "main",
    "commit_sha": "1a2b3c4d5e6f...40 hex chars",
    "license": "apache-2.0",
    "gated": false
  },
  "payload": {
    "hash_algorithm": "sha256",
    "chunk_size_bytes": 4089446400,
    "file_count": 14,
    "total_bytes": 15234567890,
    "files": [
      {
        "path": "config.json",
        "bytes": 663,
        "sha256": "64 hex chars"
      },
      {
        "path": "model-00001-of-00004.safetensors",
        "bytes": 4877611040,
        "sha256": "64 hex chars (hash of the ORIGINAL whole file)",
        "parts": [
          { "name": "model-00001-of-00004.safetensors.mfpart0000", "path": "model-00001-of-00004.safetensors.mfpart0000", "bytes": 4089446400, "sha256": "..." },
          { "name": "model-00001-of-00004.safetensors.mfpart0001", "path": "model-00001-of-00004.safetensors.mfpart0001", "bytes": 788164640, "sha256": "..." }
        ]
      }
    ]
  },
  "verifier": {
    "path": "tools/modelferry_offline.py",
    "sha256": "64 hex chars"
  }
}
```

Rules:

- `files[].path` is POSIX-style, relative, normalized. No leading `/`, no `..` segments, no drive letters, no symlinks. Both writer and reader enforce this.
- `parts[].path` is the payload-relative location of a part. It follows the same path rules as `files[].path`, and must equal `dirname(files[].path)` joined with `parts[].name` (POSIX). The reader enforces this exact layout and rejects any other.
- `parts[].name` is the part's own filename: a single path segment (no `/`) equal to `basename(files[].path)` + `.mfpart` + exactly four decimal digits (e.g. `model-00001-of-00004.safetensors.mfpart0000`). The reader enforces this and rejects any other name.
- Whole-file `sha256` is always present, including for chunked files, so unpacked output can be re-verified against the manifest forever.
- `license` comes from repo metadata. If it cannot be determined, the literal string `"UNKNOWN"` (and MANIFEST.md flags it prominently).
- `manifest_version` is an integer. After Phase 2 the format is frozen: any change to structure or semantics bumps the version, and offline.py must reject versions it does not know with exit code 2 and a clear message.

## 6. MANIFEST.md (officer-facing)

Generated at pack time. Contains, in order:

1. Header: bundle name and an intro that frames the document as the approval record used twice. Before transfer, review and approve the details and keep a copy. On arrival, the check routes through `inspect`: the manifest checksum `inspect` recomputes from the bytes on disk is compared to the checksum in the approved copy, and `verify` must report OK. The comparison is retained-copy against recomputed-from-disk, never a claim inside the arrived document against another claim inside it.
2. Source: repo id, commit SHA, revision requested, license (UNKNOWN gets a warning box), gated, endpoint, created-at, tool version.
3. Totals: file count, payload objects on media (whole files plus generated parts, equal to what `verify` reports as objects checked), total bytes (human units and exact), chunk size. When objects exceed files, a sentence explains that chunked files are split into parts and points at the Parts column.
4. The sha256 of manifest.json, framed as the value the approved copy carries and the value the receiving side checks against via `inspect` (not a comparison to be made against another value printed in the arrived document).
5. Verifier: path and sha256 of the bundled offline.py. The retained approved copy anchors the verifier hash out-of-band per §9, so a receiving site can confirm the bundled verifier or bring its own.
6. Verification instructions: the two-command sequence run from the bundle directory,
   ```
   python3 tools/modelferry_offline.py inspect .
   python3 tools/modelferry_offline.py verify .
   ```
   with a note that this requires only Python 3.9+ and no network or packages, that `inspect` prints the recomputed `manifest_sha256` to compare against the approved copy, and that `verify` prints "verify OK" only when every object matches.
7. Full file table: path, size in bytes, Parts (object count for that file: 1 stored whole, N when chunked, summing to the Totals objects line), whole-file sha256. Complete hashes, not truncated.

Prose style: plain sentences, contractions fine, no marketing language, no em dashes.

## 7. offline.py constraints

`src/modelferry/offline.py` is the trust surface. Hard requirements:

- Python standard library only. No third-party imports, no imports from the `modelferry` package. Enforced by an AST test (§11).
- Runs on CPython 3.9 (RHEL 9 system Python). No syntax or stdlib features newer than 3.9.
- One self-contained file. Pack copies this exact file into every bundle at `tools/modelferry_offline.py`.
- Has its own `argparse`-based `__main__` with subcommands `verify`, `unpack`, `inspect` mirroring §3 semantics.
- Contains its own manifest reader. Do not share a parsing module with the pack side; the round-trip test in §11 keeps writer and reader honest.
- Soft cap 550 lines including docstrings, so a human can review the whole file in one sitting (raised from 500 in phase 2.2 to fit the symlink, atomic-join, and part-name hardening). If it grows past that, simplify rather than split.
- Progress output: plain prints (files done / total, current file). No dependencies means no fancy bars, and that is fine.

Behavior:

- `verify`: check manifest.sha256 matches manifest.json; recompute the sha256 of every on-disk object (each part for chunked files, whole file otherwise) with streamed reads; report per-file status OK / MISMATCH / MISSING; report files present under `payload/` but absent from the manifest as EXTRA. verify also recomputes the sha256 of the bundled `tools/modelferry_offline.py` and compares it to `verifier.sha256`; a mismatch is exit 1. This self-check catches accidental corruption of the verifier only; tamper resistance stays out-of-band per §9. Any MISMATCH, MISSING, or EXTRA means exit 1. `--quiet` prints only the summary line and failures.
- `unpack`: run verify first unless `--no-verify`. Refuse a non-empty DEST_DIR unless `--force`. Stream-join parts into the original tree under DEST_DIR (fixed read buffer, §8). After joining a chunked file, recompute its whole-file sha256 and compare to the manifest. Write `UNPACK_RECEIPT.json` into DEST_DIR: bundle name, manifest sha256, timestamp, verified true/false, tool path. Exit 1 on any integrity failure.
- `inspect`: print the §6 header and totals from the manifest. No hashing.
- Path safety on unpack (zip-slip): for every manifest path, reject absolute paths, reject any `..` segment, and confirm the resolved destination stays inside DEST_DIR. Never create symlinks. Violation is exit code 1 with an explicit security message.

## 8. Streaming and chunking rules

- All payload IO uses a fixed buffer (8 MiB). No payload file is ever fully read into memory. This is non-negotiable; bundles routinely exceed RAM.
- Pack reads each source file exactly once: the read stream feeds the whole-file hasher and, in the same pass, the current part's writer and per-part hasher. Parts roll over at the chunk boundary.
- Edge cases that must work and are unit-tested: 0-byte file, 1-byte file, size exactly equal to chunk size (one part, no empty trailing part), chunk size + 1, and a file spanning 3+ parts.
- After writing a bundle, pack runs the offline verify logic against it (read-back self-check) and fails loudly on mismatch. This catches disk-level write errors at pack time instead of inside the air gap.

## 9. Security requirements and trust model

- No secrets in bundles or logs. `HF_TOKEN` is read from the environment, used for hub calls, and never written anywhere. A test packs with a fake token in the env and asserts the token bytes appear nowhere in the bundle (§11).
- Unpack is zip-slip safe per §7.
- Trust model, stated honestly (this section is reproduced in the README):
  - v1 protects against accidental corruption, incomplete transfers, media errors, and casual tampering with payload files.
  - v1 does not protect against an adversary who can modify the payload, the manifest, and the bundled verifier together. That requires signature verification with an out-of-band key, which is v1.1 (minisign, §14).
  - Mitigation available today: `manifest.json` records the sha256 of the bundled offline.py, and each release publishes the canonical offline.py hash in the release notes, so a receiving site can check the verifier out-of-band or bring their own copy.

## 10. Errors and exit codes

```
0  success
1  integrity failure (verify mismatch/missing/extra, unpack hash failure, path-safety violation)
2  usage error (bad arguments, unknown manifest_version, malformed manifest)
3  source error (network failure, HF auth/404/gated-without-token)
4  local filesystem error (permissions, disk full, dest exists without --force)
```

Every error message states what failed, the file or path involved, and the next action for the user. No bare tracebacks for anticipated failures; tracebacks are for bugs.

## 11. Testing requirements

Unit (no network, no docker):

- Chunk split/join round-trip across the §8 edge-case sizes, byte-identical output.
- Manifest writer determinism: same inputs, byte-identical manifest.json.
- Writer/reader round-trip: bundles written by pack-side code parse and verify with offline.py.
- Path-safety table: absolute path, `..` traversal, and outside-dest resolution are all rejected.
- Corruption: flip one byte in a part, verify exits 1 and names the file; delete a part, MISSING; add a stray file to payload/, EXTRA; edit manifest.json, sidecar mismatch caught.
- Stdlib lint: AST-parse offline.py; every import resolves to `sys.stdlib_module_names`; nothing imports the modelferry package. Also assert the file compiles with `python3.9` semantics if a 3.9 interpreter is available in CI.
- Token leak: pack with `HF_TOKEN=hf_FAKESECRET123` against a local fixture, then scan every byte of the bundle for the token. Must be absent.

Integration (marked `@pytest.mark.network`):

- Pack `hf-internal-testing/tiny-random-gpt2` (a few MB), verify, unpack, byte-compare unpacked tree with the staging snapshot.

Air-gap end-to-end (`scripts/e2e_airgap.sh`, run in CI and locally):

1. Pack the tiny repo on the host with a small `--chunk-size` (e.g. `1M`) to force real chunking.
2. `docker run --network none -v bundle:/b python:3.9-slim python /b/tools/modelferry_offline.py verify /b` and then `unpack`.
3. Repeat step 2 on `rockylinux:9` using its system python3.
4. Any network syscall attempt or nonzero exit fails the script.

Do not weaken a failing corruption or security test to make it pass. Fix the code.

## 12. Repository layout and tooling

```
modelferry/
  pyproject.toml            hatchling backend; deps: typer, rich, huggingface_hub
  src/modelferry/
    __init__.py             __version__
    cli.py                  Typer app; pack + wrappers for offline commands
    pack.py                 connected-side orchestration (§3, §8)
    hf.py                   hub metadata + snapshot download (commit resolution, license, gated flag)
    manifest.py             manifest construction + MANIFEST.md rendering (pack side only)
    offline.py              §7; the only file that ships inside bundles
  tests/
  scripts/e2e_airgap.sh
  .github/workflows/ci.yml  jobs: lint+unit, integration (network), airgap-e2e (docker)
  .github/workflows/release.yml   on tag: build, publish to PyPI via trusted publishing
  CLAUDE.md  SPEC.md  README.md  CHANGELOG.md  LICENSE (Apache-2.0)
```

- Package requires Python >= 3.10. offline.py alone holds the 3.9 floor.
- uv for dev environment, ruff for lint and format, pytest for tests.
- Runtime dependencies are exactly three: typer, rich, huggingface_hub. Adding a fourth requires a spec change.

## 13. Build phases

1. Scaffold: pyproject, src layout, stub Typer app, ruff config, CI skeleton (lint + unit jobs), Apache-2.0 LICENSE.
2. offline.py first, plus its full unit test set from §11 (round-trip against hand-built fixture bundles, corruption, path safety, stdlib lint). The manifest format freezes when this phase merges.
3. Pack side: hf.py, pack.py, manifest.py, CLI wiring, MANIFEST.md template, post-pack self-verify, integration test with the tiny repo.
4. Hardening: e2e_airgap.sh + docker CI jobs (python:3.9-slim and rockylinux:9), token-leak test, include/exclude patterns, error-message and exit-code audit against §10.
5. Release: README (war-story intro, 5-minute quickstart using the tiny repo, trust-model section from §9), terminal GIF, CHANGELOG, TestPyPI dry run, PyPI trusted publishing, tag v0.1.0.

Manual checks outside CI, before announcing: pack a ~15 GB repo (e.g. Qwen2.5-7B-Instruct) watching memory stay flat; one physical USB FAT32 round-trip; one 30-80 GB pack overnight.

## 14. Parking lot (not v1)

- minisign signing of manifest.json and offline.py, `--sign` / `verify --key`.
- `--repo-type dataset`.
- Additional payload types: docker image tars, pip wheelhouses.
- Multi-volume spanning with per-volume manifests.
- SBOM / SPDX export of the manifest.
- Windows CI, resumable verify for very large bundles.
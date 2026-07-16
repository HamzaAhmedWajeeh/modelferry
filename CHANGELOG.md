# Changelog

All notable changes to modelferry are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-16

First release.

### Added

- `modelferry pack REPO_ID --dest DIR`: download a Hugging Face model repo pinned
  to a commit, split oversized files into `.mfpartNNNN` parts, hash everything
  with sha256, and write a bundle with `manifest.json`, `MANIFEST.md`, the
  payload, and a copy of the offline verifier.
- `modelferry verify`, `unpack`, and `inspect`: thin wrappers over the exact
  standard-library verifier that ships inside every bundle, so the installed CLI
  and the bundled tool behave identically.
- `src/modelferry/offline.py`: the self-contained verifier and unpacker. Standard
  library only, runs on CPython 3.9, no network, no third-party packages. Copied
  verbatim into every bundle at `tools/modelferry_offline.py`.
- `manifest.json` (version 1): deterministic, `sort_keys` serialization with
  whole-file and per-part sha256 hashes, source repo and resolved commit SHA,
  license, gated flag, and the verifier hash.
- `MANIFEST.md`: human-readable approval document for security review.
- Chunking sized for FAT32 by default (`--chunk-size 3900M`), with `--include` /
  `--exclude` fnmatch filtering and resumable downloads via `--staging`.
- Streamed IO with a fixed 8 MiB buffer throughout, so bundles larger than RAM
  pack and verify with flat memory use.
- Zip-slip-safe unpack: absolute paths, `..` segments, and anything resolving
  outside the destination are rejected, and symlinks are never created.
- Post-pack read-back self-verify, so disk-level write errors surface before the
  bundle leaves the connected side.
- Exit codes per SPEC section 10 (0 success, 1 integrity, 2 usage, 3 source,
  4 filesystem).

### Security

- `HF_TOKEN` is read from the environment only and is never written into a
  bundle, manifest, receipt, or log. A test packs with a fake token and asserts
  the token bytes appear nowhere in the output.
- The trust model is documented in the README. v1 covers accidental corruption,
  incomplete transfers, media errors, and casual tampering. It does not cover a
  coordinated adversary who rewrites payload, manifest, and verifier together;
  that needs out-of-band signature verification, planned for v1.1. Each release
  publishes the canonical `offline.py` sha256 so a receiving site can check the
  bundled verifier out-of-band.

[Unreleased]: https://github.com/HamzaAhmedWajeeh/modelferry/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/HamzaAhmedWajeeh/modelferry/releases/tag/v0.1.0

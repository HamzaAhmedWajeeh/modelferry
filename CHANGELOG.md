# Changelog

All notable changes to modelferry are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-07-22

Documentation and packaging. No code changes: `offline.py` and all functionality
are unchanged from 0.2.0, so a 0.2.1 bundle verifies identically and the bundled
verifier's canonical sha256 is the same.

### Changed

- The README documents `pack --sign` and `verify-signature` in the usage and
  quickstart sections, with a runnable "Signing a bundle" example, instead of only
  in the trust-model discussion. `--sign` is listed among the pack flags, and the
  manifest section notes the optional `signing` block and the `manifest.json.sig`
  sidecar.

### Fixed

- The demo GIF in the README uses an absolute raw URL, so it renders on the PyPI
  project page (relative paths do not resolve there).
- The package author metadata is set instead of the placeholder.

## [0.2.0] - 2026-07-22

Signing. A bundle's manifest can now be signed, and its authenticity checked
against a trusted key, without adding anything to the bare-host integrity
verifier. Signing is additive: unsigned bundles pack and verify exactly as before.

### Added

- `modelferry pack --sign`: produce a signed bundle. The manifest gains a
  `signing` block (algorithm, key id, signature filename), and a detached ed25519
  signature over the exact `manifest.json` bytes is written to `manifest.json.sig`.
  The signing key is read from the `MODELFERRY_SIGNING_KEY` environment variable
  (a path), never a flag and never written into any bundle, manifest, sidecar, or
  log. `--sign` without a key configured is a usage error, never a silent unsigned
  pack, and pack self-verifies the signature it just wrote before the bundle ships.
- `modelferry verify-signature BUNDLE_DIR --public-key PATH`: check a bundle's
  authenticity against a trusted public key. Reports VALID (exit 0), or UNSIGNED /
  BAD_SIGNATURE / KEY_MISMATCH / MISSING_SIG (exit 1) or MALFORMED (exit 2). It is
  connected-side / appliance-side, holds the trusted key, and is never copied into
  a bundle. The trusted key comes from `--public-key` or `MODELFERRY_PUBLIC_KEY`.
- `manifest.json` schema 2: an optional top-level `signing` block. Version 2 is a
  strict superset of version 1. An unsigned bundle is a version-2 manifest with no
  signing block, byte-for-byte what version 1 was plus the bumped version number.
- `MANIFEST.md` gains a Signature section for signed bundles, telling the reviewer
  how to check authenticity with `verify-signature` and stating that it augments,
  not replaces, the approved-copy checksum comparison.
- `src/modelferry/signing.py` and `src/modelferry/verify_signature.py`: the
  connected-side signer and signature verifier, ed25519 via PyNaCl.

### Changed

- `src/modelferry/offline.py` accepts manifest schema 1 and 2 for integrity. That
  is its only change: it still imports no crypto, never verifies a signature, and
  ignores the signing block (which is not under `payload.files`, so the integrity
  hashing never touches it). The bundled verifier stays standard-library only and
  runs on CPython 3.9. Its canonical sha256 therefore differs from 0.1.0; the new
  hash is published in these release notes.
- Runtime dependency added: `pynacl`, for connected-side signing only. It never
  enters `offline.py`.

### Security

- The trust model, stated in the README and SPEC section 9: integrity (the arrived
  bytes match the manifest) is checked on the bare disconnected host by the bundled
  `offline.py`, with no network and no key. Authenticity (the manifest was signed
  by a trusted key) is checked by `verify-signature` on the connected / approval
  side, where the key lives. A bundle an attacker rebuilt from scratch passes its
  own integrity check but fails signature verification against the real key.
- modelferry has no signing key of its own. `pack --sign` signs a user's bundles
  with the user's key; the trusted public key is distributed out-of-band by the
  approval authority for the receiving environment, not by modelferry. The `key_id`
  in the manifest tells a verifier which key a bundle claims; trust in that key
  comes from the operator's out-of-band distribution. This release publishes the
  canonical `offline.py` verifier hash, as every release does, and no signing key.

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

[Unreleased]: https://github.com/HamzaAhmedWajeeh/modelferry/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/HamzaAhmedWajeeh/modelferry/releases/tag/v0.2.1
[0.2.0]: https://github.com/HamzaAhmedWajeeh/modelferry/releases/tag/v0.2.0
[0.1.0]: https://github.com/HamzaAhmedWajeeh/modelferry/releases/tag/v0.1.0

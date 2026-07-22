"""Manifest schema 2 tests (BUILD_PLAN Phase 0, task 0.3).

These drive manifest.build_manifest directly (no pack, no offline), so they are
unaffected by offline.py still accepting only v1 until task 0.4. They cover the
optional signing block: deterministic serialization with a block, omit-when-
unsigned, and the exact block shape with algorithm == "ed25519".
"""

import json

from modelferry import manifest

TOOL = {"name": "modelferry", "version": "0.2.0", "python": "3.12.0", "platform": "test"}
SOURCE = {
    "type": "huggingface",
    "endpoint": "https://huggingface.co",
    "repo_id": "acme/demo-model",
    "repo_type": "model",
    "revision_requested": "main",
    "commit_sha": "a1b2c3d4" + "0" * 32,
    "license": "apache-2.0",
    "gated": False,
}
FILES = [
    {"path": "config.json", "bytes": 20, "sha256": "a" * 64},
    {"path": "model.safetensors", "bytes": 100, "sha256": "b" * 64},
]
VERIFIER = {"path": "tools/modelferry_offline.py", "sha256": "c" * 64}
CREATED = "2026-07-20T00:00:00Z"
KEY_ID = "5f3c1a9b0d7e2f84"


def _build(signing=None):
    return manifest.build_manifest(
        bundle_name="demo-model__a1b2c3d",
        created_at=CREATED,
        tool=TOOL,
        source=SOURCE,
        chunk_size_bytes=0,
        files=list(FILES),
        verifier=dict(VERIFIER),
        signing=signing,
    )


def test_manifest_v2_deterministic():
    # Same inputs including a signing block -> byte-identical manifest.json across
    # two independently constructed builds.
    b1 = manifest.serialize(_build(signing=manifest.signing_block(key_id=KEY_ID)))
    b2 = manifest.serialize(_build(signing=manifest.signing_block(key_id=KEY_ID)))
    assert b1 == b2
    man = json.loads(b1)
    assert man["manifest_version"] == 2
    assert "signing" in man


def test_v2_unsigned_omits_signing_key():
    # No signing arg -> the "signing" key is absent entirely, not present as null.
    man = _build(signing=None)
    assert man["manifest_version"] == 2
    assert "signing" not in man
    round_tripped = json.loads(manifest.serialize(man))
    assert "signing" not in round_tripped


def test_v2_signing_block_shape():
    man = _build(signing=manifest.signing_block(key_id="deadbeefcafe0000"))
    sig = man["signing"]
    # Exactly these three fields, nothing more.
    assert set(sig) == {"algorithm", "key_id", "signature_file"}
    assert sig["algorithm"] == "ed25519"  # not "minisign-ed25519" (0.2 decision)
    assert sig["key_id"] == "deadbeefcafe0000"
    assert sig["signature_file"] == "manifest.json.sig"
    # Circularity guard: the block carries nothing derived from the signature.
    assert "signature" not in sig

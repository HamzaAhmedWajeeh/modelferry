"""Signature-verifier tests (BUILD_PLAN Phase 0, task 0.5).

These build REAL signed bundles with signing.py + manifest.py, so the producer
(Ed25519Signer.sign) and the verifier (verify_bundle_signature) are exercised
together end-to-end. Connected-side, so PyNaCl is fair game.

The verifier reads only manifest.json and the detached signature sidecar, so a
test bundle is just those two files; no payload or offline sidecar is needed.
"""

import hashlib

import pytest
from typer.testing import CliRunner

from modelferry import manifest
from modelferry.cli import app
from modelferry.signing import SIGNING_KEY_ENV, Ed25519Signer
from modelferry.verify_signature import (
    BAD_SIGNATURE,
    KEY_MISMATCH,
    MALFORMED,
    MISSING_SIG,
    UNSIGNED,
    VALID,
    PublicKeyError,
    load_public_key,
    verify_bundle_signature,
)

runner = CliRunner()

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
FILES = [{"path": "config.json", "bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}]
VERIFIER = {"path": "tools/modelferry_offline.py", "sha256": "c" * 64}


def _make_signer(tmp_path, monkeypatch, name="key"):
    secret = tmp_path / f"{name}.sec"
    public = tmp_path / f"{name}.pub"
    Ed25519Signer.generate_keypair(secret, public)
    monkeypatch.setenv(SIGNING_KEY_ENV, str(secret))
    return Ed25519Signer(), public


def _manifest_bytes(signing=None):
    man = manifest.build_manifest(
        bundle_name="demo-model__a1b2c3d",
        created_at="2026-07-20T00:00:00Z",
        tool=TOOL,
        source=SOURCE,
        chunk_size_bytes=0,
        files=list(FILES),
        verifier=dict(VERIFIER),
        signing=signing,
    )
    return manifest.serialize(man)


def _write_signed_bundle(
    bundle_dir, signer, declared_key_id=None, write_sidecar=True, tamper=False
):
    """Write manifest.json + its detached .sig. Options exercise the failure modes.

    declared_key_id overrides the key_id in the signing block (for KEY_MISMATCH).
    tamper flips a manifest byte AFTER signing (for BAD_SIGNATURE). write_sidecar
    False omits the .sig file (for MISSING_SIG).
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    key_id = declared_key_id if declared_key_id is not None else signer.key_id()
    raw = _manifest_bytes(signing=manifest.signing_block(key_id=key_id))
    signature = signer.sign(raw)  # over the exact serialized bytes
    if tamper:
        # Change a byte inside a JSON string value so the manifest stays valid
        # JSON but its bytes differ; the detached signature over the original no
        # longer verifies -> BAD_SIGNATURE. (A tamper that breaks JSON structure
        # instead surfaces as MALFORMED, which is also rejected, exit 2.)
        assert b"apache-2.0" in raw
        raw = raw.replace(b"apache-2.0", b"apache-9.0")
    (bundle_dir / "manifest.json").write_bytes(raw)
    if write_sidecar:
        (bundle_dir / manifest.SIGNATURE_FILENAME).write_bytes(signature)
    return bundle_dir


def test_good_signature_verifies(tmp_path, monkeypatch):
    signer, _ = _make_signer(tmp_path, monkeypatch)
    bundle = _write_signed_bundle(tmp_path / "b", signer)
    res = verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == VALID
    assert res.exit_code == 0


def test_tampered_manifest_fails(tmp_path, monkeypatch):
    # Core anti-tamper proof: sign, then change a byte inside a manifest value
    # (keeping valid JSON). The signature is over the original bytes, so it no
    # longer verifies.
    signer, _ = _make_signer(tmp_path, monkeypatch)
    bundle = _write_signed_bundle(tmp_path / "b", signer, tamper=True)
    res = verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == BAD_SIGNATURE
    assert res.exit_code == 1


def test_wrong_key_fails_bad_signature(tmp_path, monkeypatch):
    # Key A signs; verify against key B's public key. B never signed these bytes,
    # so the signature does not verify at all -> BAD_SIGNATURE (not KEY_MISMATCH).
    signer_a, _ = _make_signer(tmp_path, monkeypatch, name="A")
    _, public_b = _make_signer(tmp_path, monkeypatch, name="B")
    pub_b = load_public_key(public_b)
    bundle = _write_signed_bundle(tmp_path / "b", signer_a)
    res = verify_bundle_signature(str(bundle), pub_b)
    assert res.outcome == BAD_SIGNATURE
    assert res.exit_code == 1


def test_wrong_key_fails_key_mismatch(tmp_path, monkeypatch):
    # KEY_MISMATCH is the distinct case: the signature IS valid for the trusted
    # key (A signed these exact bytes), but the manifest's declared key_id is not
    # A's id. The signature covers the whole manifest incl. the wrong key_id, so
    # it still verifies; the key_id consistency check is what catches it.
    signer_a, _ = _make_signer(tmp_path, monkeypatch, name="A")
    bundle = _write_signed_bundle(tmp_path / "b", signer_a, declared_key_id="0" * 16)
    res = verify_bundle_signature(str(bundle), signer_a.public_key_bytes)
    assert res.outcome == KEY_MISMATCH
    assert res.exit_code == 1


def test_missing_signature_file_fails(tmp_path, monkeypatch):
    signer, _ = _make_signer(tmp_path, monkeypatch)
    bundle = _write_signed_bundle(tmp_path / "b", signer, write_sidecar=False)
    res = verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == MISSING_SIG
    assert res.exit_code == 1


def test_unsigned_bundle_reported(tmp_path, monkeypatch):
    # A v2 manifest with no signing block: UNSIGNED, not VALID, and not exit 0.
    signer, _ = _make_signer(tmp_path, monkeypatch)
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "manifest.json").write_bytes(_manifest_bytes(signing=None))
    res = verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == UNSIGNED
    assert res.outcome != VALID
    assert res.exit_code == 1


def test_malformed_signing_block(tmp_path, monkeypatch):
    # Signing block missing key_id -> MALFORMED / exit 2.
    signer, _ = _make_signer(tmp_path, monkeypatch)
    bundle = tmp_path / "b"
    bundle.mkdir()
    bad_block = {"algorithm": "ed25519", "signature_file": manifest.SIGNATURE_FILENAME}
    (bundle / "manifest.json").write_bytes(_manifest_bytes(signing=bad_block))
    res = verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == MALFORMED
    assert res.exit_code == 2


def test_malformed_bad_public_key_file(tmp_path):
    bad = tmp_path / "bad.pub"
    bad.write_text("not hex at all\n", encoding="utf-8")
    with pytest.raises(PublicKeyError):
        load_public_key(bad)


def test_unsafe_signature_file_name_rejected(tmp_path, monkeypatch):
    # A manifest that points signature_file outside the bundle is MALFORMED, not a
    # path-traversal read.
    signer, _ = _make_signer(tmp_path, monkeypatch)
    bundle = tmp_path / "b"
    bundle.mkdir()
    block = manifest.signing_block(key_id=signer.key_id(), signature_file="../evil.sig")
    (bundle / "manifest.json").write_bytes(_manifest_bytes(signing=block))
    res = verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == MALFORMED
    assert res.exit_code == 2


def test_cli_verify_signature_good_and_tampered(tmp_path, monkeypatch):
    # End-to-end through the CLI: exit 0 on a good bundle, exit 1 when tampered.
    signer, public = _make_signer(tmp_path, monkeypatch)
    good = _write_signed_bundle(tmp_path / "good", signer)
    result = runner.invoke(app, ["verify-signature", str(good), "--public-key", str(public)])
    assert result.exit_code == 0, result.stdout
    assert VALID in result.stdout

    bad = _write_signed_bundle(tmp_path / "bad", signer, tamper=True)
    result = runner.invoke(app, ["verify-signature", str(bad), "--public-key", str(public)])
    assert result.exit_code == 1

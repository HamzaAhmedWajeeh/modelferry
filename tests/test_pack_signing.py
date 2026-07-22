"""Pack-side signing tests (BUILD_PLAN Phase 0, task 0.6): `pack --sign` end to end.

Signing is opt-in and additive. These pack REAL bundles with a signer and confirm
the signing block, the .sig sidecar, the pack-time signature self-check (proven
non-trivial: a corrupted signature makes pack fail), byte determinism, and that
integrity still verifies. Connected-side, so PyNaCl is allowed.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from _bundle import deterministic_bytes, run_offline
from modelferry import pack, verify_signature
from modelferry.cli import app
from modelferry.errors import LocalFsError, UsageError
from modelferry.signing import SIGNING_KEY_ENV, Ed25519Signer

runner = CliRunner()
CHUNK = 1024
PINNED_TOOL = {"name": "modelferry", "version": "0.2.0", "python": "3.12.0", "platform": "test"}
PINNED_CREATED = "2026-07-20T00:00:00Z"
# A chunked file (3 parts + remainder) plus a whole file, so signing coexists with
# real chunking.
SAMPLE = {"config.json": b'{"n": 1}\n', "model.safetensors": deterministic_bytes(3 * CHUNK + 7)}


def _source():
    return {
        "type": "huggingface",
        "endpoint": "https://huggingface.co",
        "repo_id": "acme/demo-model",
        "repo_type": "model",
        "revision_requested": "main",
        "commit_sha": "a1b2c3d4" + "0" * 32,
        "license": "apache-2.0",
        "gated": False,
    }


def _snapshot(tmp_path, files):
    snap = tmp_path / "snap"
    for rel, data in files.items():
        disk = snap.joinpath(*rel.split("/"))
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(data)
    return snap


def _make_signer(tmp_path, monkeypatch, name="key"):
    secret = tmp_path / f"{name}.sec"
    public = tmp_path / f"{name}.pub"
    Ed25519Signer.generate_keypair(secret, public)
    monkeypatch.setenv(SIGNING_KEY_ENV, str(secret))
    return Ed25519Signer()


def _pack_signed(tmp_path, monkeypatch, dest="out", **kw):
    signer = _make_signer(tmp_path, monkeypatch)
    snap = _snapshot(tmp_path, SAMPLE)
    bundle = pack.write_bundle(
        str(snap), list(SAMPLE), str(tmp_path / dest), CHUNK, _source(), signer=signer, **kw
    )
    return signer, Path(bundle)


def test_pack_signs_when_key_present(tmp_path, monkeypatch):
    signer, bundle = _pack_signed(tmp_path, monkeypatch)
    man = json.loads((bundle / "manifest.json").read_text())
    assert man["signing"]["algorithm"] == "ed25519"
    assert man["signing"]["key_id"] == signer.key_id()
    assert man["signing"]["signature_file"] == "manifest.json.sig"
    assert (bundle / "manifest.json.sig").exists()
    res = verify_signature.verify_bundle_signature(str(bundle), signer.public_key_bytes)
    assert res.outcome == verify_signature.VALID


def test_pack_unsigned_without_sign_flag(tmp_path):
    # No signer: unchanged from before signing existed.
    snap = _snapshot(tmp_path, SAMPLE)
    bundle = Path(
        pack.write_bundle(str(snap), list(SAMPLE), str(tmp_path / "out"), CHUNK, _source())
    )
    man = json.loads((bundle / "manifest.json").read_text())
    assert "signing" not in man
    assert not (bundle / "manifest.json.sig").exists()
    code, out, err = run_offline(["verify", str(bundle)])
    assert code == 0, err + out


def test_sign_flag_without_key_fails_unit(monkeypatch):
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    with pytest.raises(UsageError) as excinfo:
        pack._make_signer(True)
    assert excinfo.value.exit_code == 2


def test_sign_flag_without_key_fails_cli(tmp_path, monkeypatch):
    # pack() builds the signer (and fails, exit 2) before hf.resolve, so no network.
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    result = runner.invoke(app, ["pack", "acme/demo", "--dest", str(tmp_path / "d"), "--sign"])
    assert result.exit_code == 2
    # No bundle claiming to be signed was produced.
    assert not list((tmp_path / "d").glob("*/manifest.json.sig"))


def test_packed_signature_self_verifies(tmp_path, monkeypatch):
    # Prove the self-check is wired, not a no-op: a corrupted signature makes pack
    # fail loudly instead of shipping a bundle whose own signature is bad.
    signer = _make_signer(tmp_path, monkeypatch)
    monkeypatch.setattr(signer, "sign", lambda raw: b"\x00" * 64)
    snap = _snapshot(tmp_path, SAMPLE)
    with pytest.raises(LocalFsError) as excinfo:
        pack.write_bundle(
            str(snap), list(SAMPLE), str(tmp_path / "out"), CHUNK, _source(), signer=signer
        )
    assert "signature self-check" in str(excinfo.value)
    assert excinfo.value.exit_code == 4


def test_signed_bundle_deterministic(tmp_path, monkeypatch):
    # ed25519 is deterministic: same inputs + same key -> byte-identical manifest
    # AND byte-identical .sig.
    signer = _make_signer(tmp_path, monkeypatch)
    snap = _snapshot(tmp_path, SAMPLE)
    common = dict(created_at=PINNED_CREATED, tool=PINNED_TOOL, signer=signer)
    b1 = Path(
        pack.write_bundle(str(snap), list(SAMPLE), str(tmp_path / "d1"), CHUNK, _source(), **common)
    )
    b2 = Path(
        pack.write_bundle(str(snap), list(SAMPLE), str(tmp_path / "d2"), CHUNK, _source(), **common)
    )
    assert (b1 / "manifest.json").read_bytes() == (b2 / "manifest.json").read_bytes()
    assert (b1 / "manifest.json.sig").read_bytes() == (b2 / "manifest.json.sig").read_bytes()


def test_signed_bundle_still_integrity_verifies(tmp_path, monkeypatch):
    # Signing is additive: the offline integrity verifier still returns OK. The .sig
    # sits in the bundle root, not payload/, so it is not flagged EXTRA.
    _, bundle = _pack_signed(tmp_path, monkeypatch)
    code, out, err = run_offline(["verify", str(bundle)])
    assert code == 0, err + out
    assert "verify OK" in out


def test_manifest_md_shows_signing_when_signed(tmp_path, monkeypatch):
    # A signed bundle's officer-facing MANIFEST.md gains a Signature section naming
    # the algorithm, the key id, the sidecar, and the verify-signature command.
    signer, bundle = _pack_signed(tmp_path, monkeypatch)
    md = (bundle / "MANIFEST.md").read_text()
    assert "## Signature" in md
    assert "ed25519" in md
    assert signer.key_id() in md
    assert "manifest.json.sig" in md
    assert "modelferry verify-signature" in md
    # The existing integrity approval flow is still present, not replaced.
    assert "## Verify" in md
    assert "approved copy" in md


def test_manifest_md_no_signing_section_when_unsigned(tmp_path):
    # An unsigned bundle's MANIFEST.md has no Signature section (unchanged).
    snap = _snapshot(tmp_path, SAMPLE)
    bundle = Path(
        pack.write_bundle(str(snap), list(SAMPLE), str(tmp_path / "out"), CHUNK, _source())
    )
    md = (bundle / "MANIFEST.md").read_text()
    assert "## Signature" not in md
    assert "verify-signature" not in md

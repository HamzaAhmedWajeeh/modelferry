"""Manifest and destination errors map to the right exit codes (SPEC section 10)."""

from pathlib import Path

from _bundle import (
    _base_manifest,
    deterministic_bytes,
    finalize_manifest,
    run_offline,
    sha256_bytes,
)

CHUNK = 1024


def _v2_bundle(bundle_dir, signing=None):
    """Build a v2 integrity bundle (one small whole file), optional signing block."""
    entry = {"path": "a.bin", "bytes": 3, "sha256": sha256_bytes(b"abc")}
    manifest = _base_manifest([entry], 3, 0)
    manifest["manifest_version"] = 2
    if signing is not None:
        manifest["signing"] = signing
    return finalize_manifest(bundle_dir, manifest, payload_files={"a.bin": b"abc"})


def _assert_one_line_error(err):
    # Anticipated failures print a single-line message, never a traceback.
    assert "Traceback" not in err
    stripped = err.strip()
    assert stripped and "\n" not in stripped


def test_unknown_manifest_version_is_usage_error(tmp_path):
    manifest = _base_manifest([], 0, 0)
    manifest["manifest_version"] = 99
    bundle = finalize_manifest(tmp_path / "bundle", manifest)
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "manifest_version" in err
    assert "99" in err


def test_unrecognized_version_3_is_usage_error(tmp_path):
    # A version the reader does not know (here 3) still exits 2, and the message
    # now names both accepted versions (task 0.4).
    manifest = _base_manifest([], 0, 0)
    manifest["manifest_version"] = 3
    bundle = finalize_manifest(tmp_path / "bundle", manifest)
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "manifest_version" in err
    assert "3" in err
    assert "1 and 2" in err
    _assert_one_line_error(err)


def test_v1_bundle_still_verifies(tmp_path, build_bundle):
    # No regression: a v1 fixture bundle (what the whole test_offline_* suite uses)
    # still verifies for integrity under the now-{1,2} reader.
    bundle = build_bundle(tmp_path / "bundle", {"a.bin": deterministic_bytes(50)}, chunk_size=CHUNK)
    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out
    assert "verify OK" in out


def test_v2_bundle_verifies_integrity(tmp_path):
    # An unsigned v2 bundle verifies for integrity.
    bundle = _v2_bundle(tmp_path / "bundle")
    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out
    assert "verify OK" in out


def test_v2_signing_block_ignored_by_integrity(tmp_path):
    # A v2 manifest carrying a signing block, with NO signature sidecar present,
    # still verifies on integrity alone. offline.py never reads or requires the
    # signature (that is the separate 0.5 tool); the signing block is not in
    # payload.files, so _iter_objects never touches it.
    signing = {
        "algorithm": "ed25519",
        "key_id": "deadbeefcafe0000",
        "signature_file": "manifest.json.sig",
    }
    bundle = _v2_bundle(tmp_path / "bundle", signing=signing)
    assert not (Path(bundle) / "manifest.json.sig").exists()
    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out
    assert "verify OK" in out


def test_malformed_json_is_usage_error(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_bytes(b"{ this is not json")
    (bundle / "manifest.sha256").write_text("deadbeef  manifest.json\n", encoding="utf-8")
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "malformed" in err


def test_missing_manifest_is_usage_error(tmp_path):
    bundle = tmp_path / "empty"
    bundle.mkdir()
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "not a bundle" in err


def test_missing_payload_files_is_usage_error(tmp_path):
    manifest = _base_manifest([], 0, 0)
    del manifest["payload"]["files"]
    bundle = finalize_manifest(tmp_path / "bundle", manifest)
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "payload.files" in err


def test_missing_verifier_block_is_usage_error(tmp_path):
    # Valid JSON, known version, complete payload, but no verifier block.
    entry = {"path": "a.bin", "bytes": 3, "sha256": sha256_bytes(b"abc")}
    manifest = _base_manifest([entry], 3, 0)
    bundle = finalize_manifest(
        tmp_path / "bundle", manifest, payload_files={"a.bin": b"abc"}, fix_verifier=False
    )
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "verifier" in err
    _assert_one_line_error(err)


def test_missing_payload_block_is_usage_error(tmp_path):
    manifest = _base_manifest([], 0, 0)
    del manifest["payload"]
    bundle = finalize_manifest(tmp_path / "bundle", manifest)
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "payload" in err
    _assert_one_line_error(err)


def test_file_entry_missing_sha256_is_usage_error(tmp_path):
    # A files[] entry missing a required field must be exit 2, not a MISMATCH or
    # a KeyError traceback.
    entry = {"path": "a.bin", "bytes": 3}  # no sha256
    manifest = _base_manifest([entry], 3, 0)
    bundle = finalize_manifest(tmp_path / "bundle", manifest, payload_files={"a.bin": b"abc"})
    code, out, err = run_offline(["verify", bundle])
    assert code == 2
    assert "file entry" in err
    _assert_one_line_error(err)


def test_nonempty_dest_without_force_is_fs_error(tmp_path, build_bundle):
    bundle = tmp_path / "bundle"
    build_bundle(bundle, {"a.bin": deterministic_bytes(10)}, chunk_size=CHUNK)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "preexisting.txt").write_text("keep me", encoding="utf-8")

    code, out, err = run_offline(["unpack", bundle, dest])
    assert code == 4
    assert "not empty" in err

    code, out, err = run_offline(["unpack", bundle, dest, "--force"])
    assert code == 0, err + out
    assert (dest / "a.bin").read_bytes() == deterministic_bytes(10)

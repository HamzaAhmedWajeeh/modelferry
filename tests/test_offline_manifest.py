"""Manifest and destination errors map to the right exit codes (SPEC section 10)."""

from _bundle import (
    _base_manifest,
    deterministic_bytes,
    finalize_manifest,
    run_offline,
    sha256_bytes,
)

CHUNK = 1024


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

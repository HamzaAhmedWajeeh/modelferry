"""Symlink rejection and atomic-join guarantees (phase 2.2 verifier hardening).

Symlink creation needs privilege on Windows, so those tests skip cleanly when it
is not permitted; Ubuntu CI creates the links and enforces the checks for real.
"""

import json

import pytest

from _bundle import build_bundle, deterministic_bytes, run_offline

CHUNK = 1024


def _symlink_or_skip(target, link, target_is_directory=False):
    import os

    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")


def test_expected_object_that_is_symlink_is_flagged(tmp_path):
    bundle = tmp_path / "bundle"
    build_bundle(
        bundle,
        {"a.bin": deterministic_bytes(20), "b.bin": deterministic_bytes(20)},
        chunk_size=CHUNK,
    )
    victim = bundle / "payload" / "a.bin"
    victim.unlink()
    _symlink_or_skip(bundle / "payload" / "b.bin", victim)

    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "SYMLINK" in out
    assert "a.bin" in out


def test_symlinked_directory_under_payload_is_flagged(tmp_path):
    bundle = tmp_path / "bundle"
    build_bundle(bundle, {"a.bin": deterministic_bytes(20)}, chunk_size=CHUNK)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(outside, bundle / "payload" / "linkdir", target_is_directory=True)

    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "SYMLINK" in out
    assert "linkdir" in out


def test_unpack_refuses_symlinked_payload_object(tmp_path):
    bundle = tmp_path / "bundle"
    build_bundle(bundle, {"a.bin": deterministic_bytes(20)}, chunk_size=CHUNK)
    real = bundle / "payload" / "real_target"
    real.write_bytes(deterministic_bytes(20))
    victim = bundle / "payload" / "a.bin"
    victim.unlink()
    _symlink_or_skip(real, victim)

    dest = tmp_path / "out"
    code, out, err = run_offline(["unpack", bundle, dest, "--no-verify"])
    assert code == 1
    assert "symlink" in err.lower()
    assert not (dest / "a.bin").exists()


def test_failed_whole_file_hash_leaves_no_file_and_no_tmp(tmp_path):
    bundle = tmp_path / "bundle"
    build_bundle(bundle, {"big.bin": deterministic_bytes(3 * CHUNK)}, chunk_size=CHUNK)
    # Corrupt only the declared whole-file sha256 so the post-join check fails.
    mpath = bundle / "manifest.json"
    manifest = json.loads(mpath.read_text())
    for entry in manifest["payload"]["files"]:
        if entry["path"] == "big.bin":
            entry["sha256"] = "0" * 64
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    dest = tmp_path / "out"
    code, out, err = run_offline(["unpack", bundle, dest, "--no-verify"])
    assert code == 1
    assert "hash check" in err
    assert not (dest / "big.bin").exists()
    assert not (dest / "big.bin.mftmp").exists()
    assert not any(p.name.endswith(".mftmp") for p in dest.rglob("*"))

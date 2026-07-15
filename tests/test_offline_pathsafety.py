"""Zip-slip path-safety table: hostile manifest paths must be rejected (exit 1)."""

import os

import pytest

from _bundle import _base_manifest, finalize_manifest, run_offline, sha256_bytes


def _whole_file_entry(path, data=b"x"):
    return {"path": path, "bytes": len(data), "sha256": sha256_bytes(data)}


def _bundle_with_file_path(bundle_dir, bad_path):
    entry = _whole_file_entry(bad_path)
    manifest = _base_manifest([entry], entry["bytes"], 0)
    return finalize_manifest(bundle_dir, manifest, payload_files=None)


HOSTILE_PATHS = [
    "/etc/passwd",  # absolute
    "../evil.bin",  # parent traversal
    "a/../../evil.bin",  # traversal through a subdir
    "..\\evil.bin",  # backslash traversal
    "C:\\Windows\\evil",  # drive-letter absolute
    "a//evil.bin",  # empty segment
]


@pytest.mark.parametrize("bad_path", HOSTILE_PATHS)
def test_verify_rejects_hostile_file_path(tmp_path, bad_path):
    bundle = _bundle_with_file_path(tmp_path / "bundle", bad_path)
    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "path safety" in err


@pytest.mark.parametrize("bad_path", HOSTILE_PATHS)
def test_unpack_rejects_hostile_file_path_and_writes_nothing_outside(tmp_path, bad_path):
    bundle = _bundle_with_file_path(tmp_path / "bundle", bad_path)
    dest = tmp_path / "out"
    # --no-verify isolates the write-side path check from the verify pass.
    code, out, err = run_offline(["unpack", bundle, dest, "--no-verify"])
    assert code == 1
    assert "path safety" in err
    # Nothing escaped the destination tree.
    assert not (tmp_path / "evil.bin").exists()


def test_hostile_part_path_layout_rejected(tmp_path):
    # Part declares a path that is not dirname(file)/name -> integrity failure.
    entry = {
        "path": "model.bin",
        "bytes": 4,
        "sha256": sha256_bytes(b"abcd"),
        "parts": [
            {
                "name": "model.bin.mfpart0000",
                "path": "../evil.mfpart0000",
                "bytes": 4,
                "sha256": sha256_bytes(b"abcd"),
            }
        ],
    }
    manifest = _base_manifest([entry], 4, 4)
    bundle = finalize_manifest(tmp_path / "bundle", manifest)
    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "path safety" in err


def test_part_name_with_traversal_rejected(tmp_path):
    # Canonical path matches name, but name itself smuggles a traversal segment.
    entry = {
        "path": "sub/model.bin",
        "bytes": 4,
        "sha256": sha256_bytes(b"abcd"),
        "parts": [
            {
                "name": "../model.bin.mfpart0000",
                "path": "sub/../model.bin.mfpart0000",
                "bytes": 4,
                "sha256": sha256_bytes(b"abcd"),
            }
        ],
    }
    manifest = _base_manifest([entry], 4, 4)
    bundle = finalize_manifest(tmp_path / "bundle", manifest)
    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "path safety" in err


def test_symlink_escape_is_contained(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        os.symlink(outside, dest / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")

    bundle = _bundle_with_file_path(tmp_path / "bundle", "link/evil.bin")
    code, out, err = run_offline(["unpack", bundle, dest, "--no-verify", "--force"])
    assert code == 1
    assert "path safety" in err
    assert not (outside / "evil.bin").exists()

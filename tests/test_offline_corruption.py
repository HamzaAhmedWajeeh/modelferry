"""Corruption detection: MISMATCH, MISSING, EXTRA, and sidecar mismatch."""

import json

from _bundle import deterministic_bytes, run_offline

CHUNK = 1024


def _chunked_bundle(tmp_path, build_bundle):
    bundle = tmp_path / "bundle"
    build_bundle(
        bundle,
        {"config.json": b"{}\n", "model.safetensors": deterministic_bytes(3 * CHUNK)},
        chunk_size=CHUNK,
    )
    return bundle


def _first_part_path(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text())
    for entry in manifest["payload"]["files"]:
        if "parts" in entry:
            return entry["parts"][0]["path"]
    raise AssertionError("no chunked file in fixture")


def test_flipped_byte_in_part_is_mismatch(tmp_path, build_bundle):
    bundle = _chunked_bundle(tmp_path, build_bundle)
    part_rel = _first_part_path(bundle)
    disk = bundle / "payload" / part_rel
    raw = bytearray(disk.read_bytes())
    raw[0] ^= 0xFF
    disk.write_bytes(bytes(raw))

    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "MISMATCH" in out
    assert part_rel in out


def test_deleted_part_is_missing(tmp_path, build_bundle):
    bundle = _chunked_bundle(tmp_path, build_bundle)
    part_rel = _first_part_path(bundle)
    (bundle / "payload" / part_rel).unlink()

    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "MISSING" in out
    assert part_rel in out


def test_stray_payload_file_is_extra(tmp_path, build_bundle):
    bundle = _chunked_bundle(tmp_path, build_bundle)
    (bundle / "payload" / "surprise.bin").write_bytes(b"not in the manifest")

    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "EXTRA" in out
    assert "surprise.bin" in out


def test_edited_manifest_trips_sidecar(tmp_path, build_bundle):
    bundle = _chunked_bundle(tmp_path, build_bundle)
    # Change manifest.json bytes but leave manifest.sha256 untouched.
    path = bundle / "manifest.json"
    path.write_bytes(path.read_bytes() + b" ")

    code, out, err = run_offline(["verify", bundle])
    assert code == 1
    assert "SIDECAR" in out or "manifest.sha256" in out

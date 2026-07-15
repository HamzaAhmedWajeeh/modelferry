"""Pack-writer unit tests (no network): round-trip against offline.py, manifest
determinism, byte-identical bundled verifier, and the pre-flight payload check.

The writer is the real pack-side code; the reader is offline.py driven as a
subprocess, exactly as SPEC section 11 requires.
"""

import hashlib
import json
from pathlib import Path

import pytest

from _bundle import deterministic_bytes, run_offline
from modelferry import pack
from modelferry.errors import UsageError

CHUNK = 1024


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


PINNED_TOOL = {"name": "modelferry", "version": "0.1.0", "python": "3.12.0", "platform": "test"}
PINNED_CREATED = "2026-07-15T00:00:00Z"


def _make_snapshot(tmp_path, files):
    snap = tmp_path / "snap"
    for rel, data in files.items():
        disk = snap.joinpath(*rel.split("/"))
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(data)
    return snap


SAMPLE = {
    "config.json": b'{"hidden_size": 8}\n',
    "tokenizer/vocab.txt": deterministic_bytes(50),
    "model.safetensors": deterministic_bytes(3 * CHUNK + 7),
    "sub/big.safetensors": deterministic_bytes(2 * CHUNK),
    "empty.bin": b"",
}


def test_writer_reader_roundtrip(tmp_path):
    snap = _make_snapshot(tmp_path, SAMPLE)
    dest = tmp_path / "out"
    dest.mkdir()
    bundle = pack.write_bundle(str(snap), list(SAMPLE), str(dest), CHUNK, _source())

    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out

    unpacked = tmp_path / "unpacked"
    code, out, err = run_offline(["unpack", bundle, str(unpacked)])
    assert code == 0, err + out
    for rel, data in SAMPLE.items():
        assert (unpacked / Path(rel)).read_bytes() == data


def test_no_chunking_stores_whole_files(tmp_path):
    snap = _make_snapshot(tmp_path, {"a.bin": deterministic_bytes(5000)})
    dest = tmp_path / "out"
    dest.mkdir()
    bundle = pack.write_bundle(str(snap), ["a.bin"], str(dest), None, _source())
    manifest = json.loads((Path(bundle) / "manifest.json").read_text())
    assert "parts" not in manifest["payload"]["files"][0]
    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out


def test_manifest_writer_determinism(tmp_path):
    snap = _make_snapshot(tmp_path, SAMPLE)
    b1 = pack.write_bundle(
        str(snap),
        list(SAMPLE),
        str(tmp_path / "d1"),
        CHUNK,
        _source(),
        created_at=PINNED_CREATED,
        tool=PINNED_TOOL,
    )
    b2 = pack.write_bundle(
        str(snap),
        list(SAMPLE),
        str(tmp_path / "d2"),
        CHUNK,
        _source(),
        created_at=PINNED_CREATED,
        tool=PINNED_TOOL,
    )
    assert (Path(b1) / "manifest.json").read_bytes() == (Path(b2) / "manifest.json").read_bytes()
    assert (Path(b1) / "manifest.sha256").read_bytes() == (
        Path(b2) / "manifest.sha256"
    ).read_bytes()


def test_bundled_verifier_is_byte_identical(tmp_path):
    snap = _make_snapshot(tmp_path, {"a.bin": deterministic_bytes(10)})
    dest = tmp_path / "out"
    dest.mkdir()
    bundle = pack.write_bundle(str(snap), ["a.bin"], str(dest), CHUNK, _source())

    source_offline = (Path(pack.__file__).parent / "offline.py").read_bytes()
    bundled = (Path(bundle) / "tools" / "modelferry_offline.py").read_bytes()
    assert bundled == source_offline

    manifest = json.loads((Path(bundle) / "manifest.json").read_text())
    assert manifest["verifier"]["sha256"] == hashlib.sha256(bundled).hexdigest()


def test_manifest_md_has_verifier_section_and_two_moments(tmp_path):
    snap = _make_snapshot(tmp_path, {"a.bin": deterministic_bytes(10)})
    dest = tmp_path / "out"
    dest.mkdir()
    bundle = pack.write_bundle(str(snap), ["a.bin"], str(dest), CHUNK, _source())

    md = (Path(bundle) / "MANIFEST.md").read_text()
    manifest = json.loads((Path(bundle) / "manifest.json").read_text())
    # Item 7: intro names both moments.
    assert "approve and retain" in md
    assert "On arrival" in md
    # Item 6: Verifier section anchors the bundled verifier hash out-of-band.
    assert "## Verifier" in md
    assert manifest["verifier"]["sha256"] in md
    assert "tools/modelferry_offline.py" in md


def test_preflight_rejects_payload_collision():
    # A literal repo file collides with a generated part name of a chunked file.
    with pytest.raises(UsageError) as excinfo:
        pack.preflight([("m.bin", 3000), ("m.bin.mfpart0000", 10)], CHUNK)
    assert "collision" in str(excinfo.value)


def test_preflight_rejects_reserved_mftmp_suffix():
    with pytest.raises(UsageError) as excinfo:
        pack.preflight([("weights.mftmp", 10)], CHUNK)
    assert "reserved" in str(excinfo.value)


def test_preflight_rejects_too_many_parts():
    with pytest.raises(UsageError) as excinfo:
        pack.preflight([("big.bin", 10001)], 1)
    message = str(excinfo.value)
    assert "parts" in message
    assert "at least" in message  # states the minimum viable chunk size
    assert "big.bin" in message

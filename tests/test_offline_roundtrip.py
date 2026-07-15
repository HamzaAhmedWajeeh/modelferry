"""Split/join round-trip over the SPEC section 8 edge sizes, byte-identical."""

import json

import pytest

from _bundle import deterministic_bytes, run_offline

CHUNK = 1024

# SPEC section 8 enumerates 0 / 1 / ==chunk / chunk+1 / 3-parts. chunk-1 is added
# per review (just-under-boundary single-object case); 2*CHUNK exercises "no empty
# trailing part" on the join side; 3*CHUNK+7 spans 4 parts with a remainder.
SIZES = [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 2 * CHUNK, 3 * CHUNK, 3 * CHUNK + 7]


@pytest.mark.parametrize("size", SIZES)
def test_single_file_roundtrip(tmp_path, build_bundle, size):
    data = deterministic_bytes(size)
    bundle = tmp_path / "bundle"
    build_bundle(bundle, {"weights.bin": data}, chunk_size=CHUNK)

    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out

    dest = tmp_path / "out"
    code, out, err = run_offline(["unpack", bundle, dest])
    assert code == 0, err + out

    assert (dest / "weights.bin").read_bytes() == data


def test_nested_and_multifile_roundtrip(tmp_path, build_bundle):
    files = {
        "config.json": b'{"hidden_size": 8}\n',
        "tokenizer/vocab.txt": deterministic_bytes(50),
        "model.safetensors": deterministic_bytes(3 * CHUNK + 500),
        "subdir/big.safetensors": deterministic_bytes(2 * CHUNK + 1),
    }
    bundle = tmp_path / "bundle"
    build_bundle(bundle, files, chunk_size=CHUNK)

    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out

    dest = tmp_path / "out"
    code, out, err = run_offline(["unpack", bundle, dest])
    assert code == 0, err + out

    for rel, data in files.items():
        assert (dest / rel).read_bytes() == data


def test_unpack_writes_receipt(tmp_path, build_bundle):
    bundle = tmp_path / "bundle"
    build_bundle(
        bundle, {"model.safetensors": deterministic_bytes(2 * CHUNK + 3)}, chunk_size=CHUNK
    )
    dest = tmp_path / "out"
    code, out, err = run_offline(["unpack", bundle, dest])
    assert code == 0, err + out

    receipt = json.loads((dest / "UNPACK_RECEIPT.json").read_text())
    assert receipt["bundle_name"] == "fixture__0000000"
    assert receipt["verified"] is True
    assert len(receipt["manifest_sha256"]) == 64
    assert receipt["unpacked_at"].endswith("Z")
    assert receipt["verifier_path"] == "tools/modelferry_offline.py"


def test_no_verify_records_unverified_receipt(tmp_path, build_bundle):
    bundle = tmp_path / "bundle"
    build_bundle(bundle, {"a.bin": deterministic_bytes(10)}, chunk_size=CHUNK)
    dest = tmp_path / "out"
    code, out, err = run_offline(["unpack", bundle, dest, "--no-verify"])
    assert code == 0, err + out
    receipt = json.loads((dest / "UNPACK_RECEIPT.json").read_text())
    assert receipt["verified"] is False

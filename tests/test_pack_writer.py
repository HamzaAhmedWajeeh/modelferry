"""Pack-writer unit tests (no network): round-trip against offline.py, manifest
determinism, byte-identical bundled verifier, and the pre-flight payload check.

The writer is the real pack-side code; the reader is offline.py driven as a
subprocess, exactly as SPEC section 11 requires.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from _bundle import deterministic_bytes, run_offline
from modelferry import manifest, pack
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
    # §6 item 1: intro frames the two uses of the document.
    assert "approval record for this bundle" in md
    assert "Before transfer" in md
    assert "On arrival" in md
    assert "Do not use it." in md
    # §6 item 6: Verifier section anchors the bundled verifier hash out-of-band.
    assert "## Verifier" in md
    assert manifest["verifier"]["sha256"] in md
    assert "tools/modelferry_offline.py" in md


def _md_sections(md):
    """Split MANIFEST.md into {heading: body_text} by '## ' headings."""
    sections = {}
    current = "_intro"
    buf = []
    for line in md.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf)
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf)
    return sections


def _parts_column_sum(md):
    """Sum the Parts column of the Files table: '| path | bytes | parts | sha |'."""
    total = 0
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells == ['', path, size, parts, sha, ''] for data rows.
        if len(cells) >= 5 and cells[3].isdigit():
            total += int(cells[3])
    return total


def test_manifest_md_object_counts_agree_with_verify(tmp_path):
    # SAMPLE has both chunked files (model.safetensors, sub/big.safetensors) and
    # whole files (config.json, empty.bin), so objects > files.
    snap = _make_snapshot(tmp_path, SAMPLE)
    dest = tmp_path / "out"
    dest.mkdir()
    bundle = pack.write_bundle(str(snap), list(SAMPLE), str(dest), CHUNK, _source())

    md = (Path(bundle) / "MANIFEST.md").read_text()
    sections = _md_sections(md)

    # The Verify section must route the retained-copy check through inspect.
    verify_body = sections["Verify"]
    assert "inspect" in verify_body
    assert "modelferry_offline.py inspect ." in verify_body
    assert "recomputes the manifest checksum" in verify_body

    # Totals declares the on-media object count.
    match = re.search(r"^- Payload objects on media: (\d+)$", md, re.MULTILINE)
    assert match, "Totals is missing the 'Payload objects on media' line"
    declared_objects = int(match.group(1))

    # The Parts column sums to that declared object count.
    assert _parts_column_sum(md) == declared_objects

    # And that count is exactly what offline.py verify reports for this bundle.
    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out
    reported = re.search(r"verify OK: (\d+) object\(s\) checked", out)
    assert reported, "verify did not print an object count: " + out
    assert int(reported.group(1)) == declared_objects


def test_manifest_md_escapes_pipe_in_path():
    # A repo file whose name contains '|' must not break the Markdown Files table.
    # Windows forbids '|' in filenames, so exercise the renderer directly rather
    # than packing a real file with that name.
    files = [{"path": "weird|name.txt", "bytes": 3, "sha256": "a" * 64}]
    man = manifest.build_manifest(
        bundle_name="x__0000000",
        created_at="2026-07-16T00:00:00Z",
        tool=PINNED_TOOL,
        source=_source(),
        chunk_size_bytes=0,
        files=files,
        verifier={"path": "tools/modelferry_offline.py", "sha256": "b" * 64},
    )
    md = manifest.render_manifest_md(man, "d" * 64)

    assert "weird\\|name.txt" in md
    assert "| weird|name.txt |" not in md  # the raw, column-breaking form
    row = next(ln for ln in md.splitlines() if ln.startswith("| weird"))
    # Exactly five unescaped pipes keeps the row at four columns.
    assert len(re.findall(r"(?<!\\)\|", row)) == 5


@pytest.mark.parametrize(
    "bad",
    [
        "sub\\weights.bin",  # backslash
        "/etc/passwd",  # absolute
        "C:/weights.bin",  # drive letter
        "../escape.bin",  # .. segment
        "dir/../escape.bin",  # .. mid-path
        "./weights.bin",  # . segment
        "dir//weights.bin",  # empty segment
    ],
)
def test_preflight_rejects_reader_unsafe_paths(bad):
    # A path offline.py's _safe_rel would reject must fail preflight (exit 2)
    # before any download, not at the post-write self-verify.
    with pytest.raises(UsageError) as excinfo:
        pack.preflight([(bad, 10)], CHUNK)
    assert excinfo.value.exit_code == 2
    # The message names the offending path; it is rendered with !r (repr).
    assert repr(bad) in str(excinfo.value)


def test_preflight_accepts_normal_nested_path():
    # Sanity: a legitimate nested repo path is not rejected.
    pack.preflight([("sub/dir/model.safetensors", 10)], CHUNK)


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

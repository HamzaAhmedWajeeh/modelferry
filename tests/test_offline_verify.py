"""verify and inspect happy paths, including --quiet behavior."""

import re

from _bundle import deterministic_bytes, run_offline

CHUNK = 1024


def _sample(tmp_path, build_bundle):
    files = {
        "config.json": b"{}\n",
        "model.safetensors": deterministic_bytes(2 * CHUNK + 5),
    }
    bundle = tmp_path / "bundle"
    build_bundle(bundle, files, chunk_size=CHUNK)
    return bundle


def test_verify_clean_bundle(tmp_path, build_bundle):
    bundle = _sample(tmp_path, build_bundle)
    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out
    assert "verify OK" in out
    # non-quiet lists per-object OK lines
    assert "OK" in out
    assert "config.json" in out


def test_verify_quiet_suppresses_ok_lines(tmp_path, build_bundle):
    bundle = _sample(tmp_path, build_bundle)
    code, out, err = run_offline(["verify", bundle, "--quiet"])
    assert code == 0, err + out
    assert "verify OK" in out
    # quiet: individual OK object lines are suppressed; only the summary remains
    assert "config.json" not in out


def test_inspect_prints_header_without_hashing(tmp_path, build_bundle):
    bundle = _sample(tmp_path, build_bundle)
    code, out, err = run_offline(["inspect", bundle])
    assert code == 0, err + out
    assert "acme/fixture" in out
    assert "apache-2.0" in out
    assert "manifest_sha256:" in out
    # inspect does no per-object hashing: no OK/MISMATCH status lines
    assert "MISMATCH" not in out
    assert "verify OK" not in out
    # the _sample bundle has one whole file plus one chunked file, so the media
    # object count exceeds the file count
    assert "objects on media: 4" in out
    assert "files:       2" in out


def test_inspect_object_count_agrees_with_verify(tmp_path, build_bundle):
    # inspect's "objects on media" must equal what verify actually checks; the
    # two counts share _iter_objects so they can never diverge. This guards that.
    bundle = _sample(tmp_path, build_bundle)

    code, out, err = run_offline(["inspect", bundle])
    assert code == 0, err + out
    m = re.search(r"objects on media: (\d+)", out)
    assert m, out
    inspect_count = int(m.group(1))

    code, vout, verr = run_offline(["verify", bundle])
    assert code == 0, verr + vout
    m = re.search(r"verify OK: (\d+) object\(s\) checked", vout)
    assert m, vout
    verify_count = int(m.group(1))

    assert inspect_count == verify_count

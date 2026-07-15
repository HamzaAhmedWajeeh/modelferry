"""verify and inspect happy paths, including --quiet behavior."""

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

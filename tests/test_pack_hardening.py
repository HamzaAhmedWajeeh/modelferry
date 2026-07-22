"""Phase 4 pack-side hardening: token-leak scan and include/exclude selection."""

from pathlib import Path

from modelferry import offline, pack
from modelferry.hf import _select

FAKE_TOKEN = "hf_FAKESECRET123"


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


def test_token_never_written_into_bundle(tmp_path, monkeypatch):
    # SPEC section 11: pack with a fake token in the environment, then scan every
    # byte of every bundle file for it. It must be absent everywhere.
    monkeypatch.setenv("HF_TOKEN", FAKE_TOKEN)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "config.json").write_bytes(b'{"model_type": "gpt2"}\n')
    (snap / "weights.bin").write_bytes(bytes(range(256)) * 12)

    # Task 0.3: the manifest is now v2, and pack's post-pack self-verify
    # (offline.cmd_verify) accepts only v1 until task 0.4, so write_bundle raises
    # after fully writing the bundle. Every bundle file (manifest.json, sidecar,
    # MANIFEST.md, verifier, payload) is on disk before that self-verify runs, so
    # the token-leak scan below stays fully valid. We deliberately keep this
    # security test live rather than xfail it (CLAUDE.md: never weaken the
    # token-leak test). Drop this shim when 0.4 makes the self-verify pass again.
    out = tmp_path / "out"
    try:
        bundle = pack.write_bundle(
            str(snap), ["config.json", "weights.bin"], str(out), 1024, _source()
        )
    except offline.UsageError:
        src = _source()
        bundle = str(out / pack._bundle_name(src["repo_id"], src["commit_sha"]))

    needle = FAKE_TOKEN.encode()
    scanned = 0
    for path in Path(bundle).rglob("*"):
        if path.is_file():
            scanned += 1
            assert needle not in path.read_bytes(), f"token leaked into {path}"
    assert scanned >= 4  # manifest.json, manifest.sha256, MANIFEST.md, verifier, payload


def test_select_applies_include_then_exclude_wins():
    files = ["config.json", "model.safetensors", "model.bin", "tok/tokenizer.json"]
    assert _select(files, None, None) == files
    assert _select(files, ["*.safetensors"], None) == ["model.safetensors"]
    assert _select(files, None, ["*.bin"]) == [
        "config.json",
        "model.safetensors",
        "tok/tokenizer.json",
    ]
    # A file matched by both include and exclude is dropped: exclude wins.
    assert _select(files, ["model.*"], ["*.bin"]) == ["model.safetensors"]

"""Tests for the connected-side signing module (BUILD_PLAN Phase 0, task 0.2).

Signing is connected-side, so PyNaCl is allowed here (unlike offline.py, whose
stdlib-only guard lives in test_offline_stdlib_lint). These tests prove the
Ed25519Signer round-trips against the public key, fails loudly with no key, and
derives key_id from the public key alone. test_signing_key_never_in_repo is the
secret-hygiene guard: no key material or absolute key-path literal is committed,
and .gitignore covers the key patterns.
"""

import hashlib
import re
import subprocess
from pathlib import Path

import pytest
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from modelferry.signing import SIGNING_KEY_ENV, Ed25519Signer, Signer, SigningError

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_keypair(tmp_path):
    """Generate a keypair in tmp_path, returning (secret_path, public_bytes)."""
    secret = tmp_path / "sec.key"
    public = tmp_path / "pub.key"
    public_bytes = Ed25519Signer.generate_keypair(secret, public)
    return secret, public_bytes


def test_signer_roundtrip(tmp_path, monkeypatch):
    secret, public_bytes = _make_keypair(tmp_path)
    monkeypatch.setenv(SIGNING_KEY_ENV, str(secret))

    signer = Ed25519Signer()
    assert isinstance(signer, Signer)  # structurally satisfies the Protocol

    message = b"canonical manifest bytes \x00\x01\x02 \xff and more"
    signature = signer.sign(message)
    assert isinstance(signature, bytes)

    verify_key = VerifyKey(public_bytes)
    # Verifies against the corresponding public key using PyNaCl directly; raises
    # BadSignatureError if the signature does not match.
    verify_key.verify(message, signature)

    # Flip one byte of the signed message; verification must now fail.
    tampered = bytearray(message)
    tampered[0] ^= 0x01
    with pytest.raises(BadSignatureError):
        verify_key.verify(bytes(tampered), signature)


def test_missing_key_env_fails(monkeypatch):
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    with pytest.raises(SigningError):
        Ed25519Signer()  # must raise, never return a no-op signer


def test_key_id_is_from_public_key(tmp_path, monkeypatch):
    secret, public_bytes = _make_keypair(tmp_path)
    monkeypatch.setenv(SIGNING_KEY_ENV, str(secret))

    signer_a = Ed25519Signer()
    signer_b = Ed25519Signer()
    # Stable across two constructions with the same key.
    assert signer_a.key_id() == signer_b.key_id()
    # Derivable from the public key alone, with no access to the signer object.
    expected = hashlib.sha256(public_bytes).hexdigest()[:16]
    assert signer_a.key_id() == expected


def _tracked_files():
    """Repo-tracked paths via git, or None if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line for line in out.stdout.splitlines() if line]


# Absolute path (POSIX /... or Windows C:\...) that ends in a key/pem extension.
_ABS_KEY_PATH = re.compile(r"""(?:[A-Za-z]:\\|/)[^\s'"]*\.(?:key|pem)\b""")


def test_signing_key_never_in_repo():
    tracked = _tracked_files()
    if tracked is None:
        pytest.skip("git not available")

    # 1. No key material files are tracked. *.key / *.pem / sec.key / a bare .env
    #    must never be committed (public repo). .env.example is allowed.
    for rel in tracked:
        name = Path(rel).name
        bad = name.endswith(".key") or name.endswith(".pem") or name == "sec.key"
        bad = bad or name == ".env"
        assert not bad, f"key/secret material is tracked in the repo: {rel}"

    # 2. .gitignore covers the key patterns the task requires.
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    patterns = {line.strip() for line in gitignore}
    assert "*.key" in patterns, ".gitignore must ignore *.key"
    assert "sec.key" in patterns, ".gitignore must ignore sec.key"

    # 3. No shipping source hardcodes an absolute path to a key file; the secret
    #    path is only ever read from MODELFERRY_SIGNING_KEY at runtime.
    for py in sorted((REPO_ROOT / "src" / "modelferry").glob("*.py")):
        text = py.read_text(encoding="utf-8")
        assert not _ABS_KEY_PATH.search(text), f"absolute key-path literal committed in {py.name}"

"""Connected-side signing for modelferry manifests.

This module is connected-side only. It never ships inside a bundle and must never
be imported by offline.py, the frozen standard-library-only integrity verifier.
Authenticity (a signature over the manifest) and integrity (offline.py's hash
checks) are deliberately separate tools with different trust models and different
environments; see BUILD_PLAN.md Phase 0 and SPEC.md sections 7 and 12. Because
this side is connected, dependencies are allowed here (PyNaCl), unlike offline.py.

A Signer takes the exact serialized manifest bytes and returns a detached
signature. The concrete Ed25519Signer is backed by PyNaCl. The secret key is read
from the path in the MODELFERRY_SIGNING_KEY environment variable, never a function
argument, never a committed file, and is never logged. key_id() is a fingerprint
of the PUBLIC key only, so it is safe to print and to record in the manifest.

The Signer Protocol is the extension point: a KMS/HSM-backed signer (Phase 3) or a
minisign-interop signer drops in without any caller change, as long as it returns
a detached signature and a stable key id.
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol, runtime_checkable

from nacl.signing import SigningKey

SIGNING_KEY_ENV = "MODELFERRY_SIGNING_KEY"
# Hex characters of the public-key fingerprint returned by key_id(). 16 hex chars
# is 64 bits, enough to name a key in a manifest and release note without being
# unwieldy. It is a fingerprint, not a secret, and is derived from the public key.
_KEY_ID_LEN = 16


def public_key_id(public_key_bytes):
    """Return the stable modelferry key id (fingerprint) for an ed25519 public key.

    Single source of truth for how a key is named: Ed25519Signer.key_id() and the
    verify_signature tool both derive the id from the public key this way, so a
    signer and a verifier always agree. Derived only from the public key.
    """
    return hashlib.sha256(bytes(public_key_bytes)).hexdigest()[:_KEY_ID_LEN]


class SigningError(Exception):
    """Raised when a signer cannot be constructed or used.

    The message never contains secret key material. It may name the configured
    key path (which came from the environment, not from source) so the operator
    can see which file failed.
    """


@runtime_checkable
class Signer(Protocol):
    """A connected-side manifest signer.

    Implementations sign the exact serialized manifest bytes and expose a stable
    identifier for the signing key. The signature is always detached: it is
    written to a sidecar next to manifest.json, never embedded in the manifest
    (a manifest cannot contain a signature over itself).
    """

    def sign(self, manifest_bytes: bytes) -> bytes:
        """Return the detached signature over manifest_bytes."""
        ...

    def key_id(self) -> str:
        """Return a stable identifier derived from the public key."""
        ...


class Ed25519Signer:
    """Ed25519 signer backed by PyNaCl.

    The secret key is read once at construction from the file named by
    MODELFERRY_SIGNING_KEY. That file holds the 32-byte ed25519 seed, hex-encoded
    (see generate_keypair). Construction fails loudly with SigningError if the env
    var is unset or empty, the file is missing or unreadable, or the contents are
    not a valid key. It never silently returns a no-op signer.
    """

    def __init__(self):
        path = os.environ.get(SIGNING_KEY_ENV)
        if not path:
            raise SigningError(
                f"{SIGNING_KEY_ENV} is not set. Point it at the ed25519 secret key file, "
                "generated outside the repo. Signing needs a key and will not proceed unsigned."
            )
        self._signing_key = self._load_signing_key(path)
        # Cache the public key bytes so key_id() and public_key_bytes never touch
        # the secret again after construction.
        self._public_key_bytes = bytes(self._signing_key.verify_key)

    @staticmethod
    def _load_signing_key(path):
        """Read and validate the ed25519 seed from path. Never logs the seed."""
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            raise SigningError(
                f"signing key file named by {SIGNING_KEY_ENV} does not exist: {path}"
            ) from None
        except OSError as e:
            raise SigningError(
                f"cannot read the signing key file named by {SIGNING_KEY_ENV}: {e}"
            ) from None
        try:
            seed = bytes.fromhex(text.strip())
        except ValueError:
            raise SigningError(
                f"signing key file named by {SIGNING_KEY_ENV} is not valid hex-encoded key material"
            ) from None
        if len(seed) != 32:
            raise SigningError(
                f"signing key named by {SIGNING_KEY_ENV} must be a 32-byte ed25519 seed "
                f"(got {len(seed)} bytes)"
            )
        return SigningKey(seed)

    def sign(self, manifest_bytes: bytes) -> bytes:
        """Return the 64-byte detached ed25519 signature over manifest_bytes."""
        if not isinstance(manifest_bytes, (bytes, bytearray)):
            raise SigningError("sign() takes the serialized manifest bytes")
        return self._signing_key.sign(bytes(manifest_bytes)).signature

    def key_id(self) -> str:
        """Return a stable fingerprint of the public key (never the secret)."""
        return public_key_id(self._public_key_bytes)

    @property
    def public_key_bytes(self) -> bytes:
        """The 32-byte ed25519 public key. Safe to publish and record."""
        return self._public_key_bytes

    @staticmethod
    def generate_keypair(secret_path, public_path):
        """Generate an ed25519 keypair and write both files, returning public bytes.

        The 32-byte seed is written hex-encoded to secret_path and the 32-byte
        public key hex-encoded to public_path. This defines the on-disk format the
        signer reads, so keys generated here and by real operators agree. The
        secret file must never be committed; .gitignore covers *.key and sec.key.
        """
        signing_key = SigningKey.generate()
        seed = bytes(signing_key)
        public = bytes(signing_key.verify_key)
        with open(secret_path, "w", encoding="utf-8") as f:
            f.write(seed.hex() + "\n")
        # Best-effort tighten permissions on POSIX; a no-op on Windows.
        try:
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
        with open(public_path, "w", encoding="utf-8") as f:
            f.write(public.hex() + "\n")
        return public

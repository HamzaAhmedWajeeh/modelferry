"""Connected-side signature verifier for modelferry bundles (authenticity).

Counterpart to signing.py. signing.py produces a detached signature over the
serialized manifest bytes; this verifies one against a trusted public key. Like
signing.py it is connected-side / appliance-side and may use PyNaCl. It is NOT
copied into bundles and is NOT the bare-host verifier.

The trust split: offline.py proves INTEGRITY (the on-disk bytes match the
manifest) on the bare air-gap host with no crypto. verify_signature.py proves
AUTHENTICITY (the manifest was signed by a trusted key) where a key and a crypto
library live. Both read the SAME manifest.json bytes, independently.

The detached signature sidecar (named by the manifest's signing.signature_file,
default manifest.json.sig) holds the raw ed25519 signature over the exact
manifest.json bytes. The trusted public key comes from a file (hex-encoded, the
format signing.py.generate_keypair writes), passed on the CLI or via env, never
embedded.

Outcomes and exit codes stay within SPEC section 10; no new exit code is invented
(CLAUDE.md). See the table in verify_bundle_signature.
"""

from __future__ import annotations

import json
import os
from collections import namedtuple

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from . import manifest as manifest_mod
from .signing import public_key_id

MANIFEST_NAME = "manifest.json"

# Outcomes. Exit codes map onto SPEC section 10: 0 success, 1 authenticity
# failure (the analogue of an integrity failure), 2 usage/malformed. UNSIGNED is
# 1, not 0: absence of a signature must never read as "verified" (see the module
# report for the full justification). No exit code outside section 10 is invented.
VALID = "VALID"
UNSIGNED = "UNSIGNED"
BAD_SIGNATURE = "BAD_SIGNATURE"
KEY_MISMATCH = "KEY_MISMATCH"
MISSING_SIG = "MISSING_SIG"
MALFORMED = "MALFORMED"

_EXIT = {
    VALID: 0,
    UNSIGNED: 1,
    BAD_SIGNATURE: 1,
    KEY_MISMATCH: 1,
    MISSING_SIG: 1,
    MALFORMED: 2,
}

SignatureResult = namedtuple("SignatureResult", ["outcome", "exit_code", "message"])


def _result(outcome, message):
    return SignatureResult(outcome, _EXIT[outcome], message)


class PublicKeyError(Exception):
    """Raised when the trusted public key file cannot be read or parsed."""


def load_public_key(path):
    """Read a hex-encoded 32-byte ed25519 public key (signing.py's on-disk format)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise PublicKeyError(f"cannot read public key file {path}: {e}") from None
    try:
        key = bytes.fromhex(text.strip())
    except ValueError:
        raise PublicKeyError(f"public key file {path} is not valid hex") from None
    if len(key) != 32:
        raise PublicKeyError(f"public key in {path} must be 32 bytes, got {len(key)}")
    return key


def _safe_sidecar_name(name):
    """True if name is a single, traversal-free filename (no separators, no ..)."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\\" not in name
        and os.path.basename(name) == name
    )


def verify_bundle_signature(bundle_dir, trusted_public_key):
    """Verify a bundle's manifest signature against trusted_public_key (32 bytes).

    Returns SignatureResult(outcome, exit_code, message). Reads the exact
    manifest.json bytes (the same bytes offline.py hashes), the signing block, and
    the detached signature sidecar, then checks both the signature and the declared
    key_id against the trusted key.

        VALID          0   signature verifies and key_id matches the trusted key
        UNSIGNED       1   no signing block: no authenticity claim (not "verified")
        BAD_SIGNATURE  1   signature present but does not verify (tamper/wrong key)
        KEY_MISMATCH   1   signature verifies but declared key_id != trusted key id
        MISSING_SIG    1   signing block present but the sidecar file is absent
        MALFORMED      2   manifest/signing block malformed, or unsupported algorithm
    """
    manifest_path = os.path.join(bundle_dir, MANIFEST_NAME)
    try:
        with open(manifest_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return _result(MALFORMED, f"cannot read {MANIFEST_NAME} in {bundle_dir}: {e}")
    try:
        man = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return _result(MALFORMED, f"malformed {MANIFEST_NAME}: {e}")
    if not isinstance(man, dict):
        return _result(MALFORMED, f"malformed {MANIFEST_NAME}: top level is not an object")

    signing = man.get("signing")
    if signing is None:
        return _result(
            UNSIGNED,
            "unsigned: this bundle carries no signing block, so there is no "
            "authenticity claim to verify. Integrity is checked separately by verify.",
        )
    if not isinstance(signing, dict):
        return _result(MALFORMED, "malformed signing block: not an object")
    algorithm = signing.get("algorithm")
    key_id = signing.get("key_id")
    sig_name = signing.get("signature_file")
    if not (isinstance(algorithm, str) and isinstance(key_id, str) and isinstance(sig_name, str)):
        return _result(
            MALFORMED, "malformed signing block: missing algorithm/key_id/signature_file"
        )
    if algorithm != manifest_mod.SIGNING_ALGORITHM:
        return _result(
            MALFORMED,
            f"unsupported signature algorithm {algorithm!r}; this verifier handles "
            f"{manifest_mod.SIGNING_ALGORITHM!r}",
        )
    if not _safe_sidecar_name(sig_name):
        return _result(MALFORMED, f"unsafe signature_file name {sig_name!r} in signing block")

    sig_path = os.path.join(bundle_dir, sig_name)
    try:
        with open(sig_path, "rb") as f:
            signature = f.read()
    except FileNotFoundError:
        return _result(
            MISSING_SIG,
            f"signing block names {sig_name} but that signature sidecar is absent",
        )
    except OSError as e:
        return _result(MALFORMED, f"cannot read signature sidecar {sig_name}: {e}")

    try:
        VerifyKey(trusted_public_key).verify(raw, signature)
    except BadSignatureError:
        return _result(
            BAD_SIGNATURE,
            "signature does not verify against the trusted public key: the manifest "
            "was altered or was not signed by this key. Do not trust this bundle.",
        )

    trusted_id = public_key_id(trusted_public_key)
    if key_id != trusted_id:
        return _result(
            KEY_MISMATCH,
            f"signature verifies but the manifest's key_id {key_id!r} does not match "
            f"the trusted key id {trusted_id!r}",
        )
    return _result(VALID, f"signature is valid and signed by the trusted key {trusted_id}")

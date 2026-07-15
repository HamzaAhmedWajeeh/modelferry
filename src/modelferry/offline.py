#!/usr/bin/env python3
"""Self-contained offline verifier/unpacker for modelferry bundles.

This file is the trust surface. It is copied verbatim into every bundle at
tools/modelferry_offline.py and is the code a receiving site runs inside the air
gap. Hard constraints (see SPEC.md sections 7 and 11):

- Standard library only. No third-party imports, no imports from the modelferry
  package. An AST test enforces this.
- Runs on CPython 3.9 (RHEL 9 system Python). No syntax or stdlib newer than 3.9.
  In particular, no PEP 604 ``X | None`` at runtime and no
  ``from __future__ import annotations`` (which would let tooling rewrite to it).
- One file, soft cap ~500 lines, reviewable in one sitting.
- All payload IO streams through a fixed 8 MiB buffer. No payload file is ever
  read fully into memory.

Subcommands mirror the installed CLI: verify, unpack, inspect.
"""

import argparse
import hashlib
import json
import ntpath
import os
import posixpath
import sys
from datetime import datetime, timezone
from typing import Optional

BUF_SIZE = 8 * 1024 * 1024
SUPPORTED_MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIDECAR_NAME = "manifest.sha256"
PAYLOAD_DIR = "payload"
RECEIPT_NAME = "UNPACK_RECEIPT.json"
DEFAULT_VERIFIER_REL = "tools/modelferry_offline.py"


# --------------------------------------------------------------------------- #
# Errors: each carries the SPEC section 10 exit code. No exit 3 (network) is
# reachable offline.
# --------------------------------------------------------------------------- #
class MferryError(Exception):
    exit_code = 1


class IntegrityError(MferryError):
    exit_code = 1  # verify mismatch/missing/extra, unpack hash failure, path safety


class UsageError(MferryError):
    exit_code = 2  # bad manifest, unknown version, not a bundle


class LocalFsError(MferryError):
    exit_code = 4  # dest exists without --force, permission, disk full


# --------------------------------------------------------------------------- #
# Streaming IO primitives (fixed 8 MiB buffer)
# --------------------------------------------------------------------------- #
def _sha256_file(path):
    """Return the hex sha256 of a file, streamed. Peak memory is one buffer."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _copyhash(src_fh, dst_fh, hasher):
    """Copy src_fh to dst_fh in fixed-size blocks, feeding hasher as we go."""
    while True:
        chunk = src_fh.read(BUF_SIZE)
        if not chunk:
            break
        dst_fh.write(chunk)
        hasher.update(chunk)


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Path safety (zip-slip). Manifest paths are POSIX, relative, normalized.
# --------------------------------------------------------------------------- #
def _safe_rel(rel):
    """Validate a payload-relative POSIX path and return its components.

    Rejects empty, absolute, drive-lettered, backslash-bearing paths and any
    '.' or '..' segment. Raises IntegrityError on violation.
    """
    if not isinstance(rel, str) or not rel:
        raise IntegrityError("path safety: empty or non-string path in manifest")
    if "\\" in rel:
        raise IntegrityError("path safety: backslash in path %r" % rel)
    if rel.startswith("/") or ntpath.splitdrive(rel)[0] or ":" in rel:
        raise IntegrityError("path safety: absolute or drive-letter path %r" % rel)
    parts = rel.split("/")
    for seg in parts:
        if seg in ("", ".", ".."):
            raise IntegrityError("path safety: illegal segment in path %r" % rel)
    return parts


def _safe_join(base, rel):
    """Join base + validated rel, then confirm it stays inside base.

    The realpath containment check also defends against an existing symlink
    under a --force destination redirecting a write outside the tree.
    """
    parts = _safe_rel(rel)
    target = os.path.join(base, *parts)
    real_base = os.path.realpath(base)
    real_target = os.path.realpath(target)
    try:
        common = os.path.commonpath([real_base, real_target])
    except ValueError:
        # Different drives on Windows, etc.
        raise IntegrityError("path safety: %r resolves outside destination" % rel) from None
    if common != real_base:
        raise IntegrityError("path safety: %r resolves outside destination" % rel)
    return target


def _part_rel(file_path, part):
    """Return the enforced payload-relative path of a part.

    SPEC section 5 requires parts[].path == dirname(files[].path)/parts[].name.
    Any other layout is rejected as an integrity failure.
    """
    name = part.get("name")
    declared = part.get("path")
    if not isinstance(name, str) or not isinstance(declared, str):
        raise IntegrityError("malformed part entry for %r (missing name/path)" % file_path)
    expected = posixpath.join(posixpath.dirname(file_path), name)
    if declared != expected:
        raise IntegrityError(
            "path safety: part path %r does not match required layout %r for %r"
            % (declared, expected, file_path)
        )
    _safe_rel(declared)
    return declared


# --------------------------------------------------------------------------- #
# Manifest loading and sidecar
# --------------------------------------------------------------------------- #
def _load_manifest(bundle_dir):
    """Read and minimally validate manifest.json. Returns (manifest, raw_bytes)."""
    path = os.path.join(bundle_dir, MANIFEST_NAME)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        raise UsageError(
            "not a bundle: %s not found in %s. Point this at a bundle directory."
            % (MANIFEST_NAME, bundle_dir)
        ) from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise UsageError("malformed %s: %s" % (MANIFEST_NAME, e)) from None
    if not isinstance(manifest, dict):
        raise UsageError("malformed %s: top level is not an object" % MANIFEST_NAME)
    version = manifest.get("manifest_version")
    if version != SUPPORTED_MANIFEST_VERSION:
        raise UsageError(
            "unsupported manifest_version %r; this verifier understands version %d. "
            "Use a matching modelferry release." % (version, SUPPORTED_MANIFEST_VERSION)
        )
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise UsageError("malformed %s: missing payload.files" % MANIFEST_NAME)
    _validate_shape(manifest)
    return manifest, raw


def _validate_shape(manifest):
    """Reject a well-formed-JSON manifest that is missing required keys (exit 2).

    A missing key must surface as a one-line usage error, not a KeyError
    traceback later in verify/unpack.
    """
    verifier = manifest.get("verifier")
    if not isinstance(verifier, dict) or "path" not in verifier or "sha256" not in verifier:
        raise UsageError("malformed manifest: missing or incomplete verifier block")
    for entry in manifest["payload"]["files"]:
        if not isinstance(entry, dict) or not all(k in entry for k in ("path", "bytes", "sha256")):
            raise UsageError("malformed manifest: file entry missing path/bytes/sha256")
        for part in entry.get("parts") or []:
            if not isinstance(part, dict) or not all(
                k in part for k in ("name", "path", "bytes", "sha256")
            ):
                raise UsageError("malformed manifest: part entry missing name/path/bytes/sha256")


def _check_sidecar(bundle_dir, manifest_bytes):
    """Return None if the sidecar matches, else an error message string."""
    path = os.path.join(bundle_dir, SIDECAR_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return "%s sidecar is missing" % SIDECAR_NAME
    fields = text.split()
    if not fields:
        return "%s is empty" % SIDECAR_NAME
    declared = fields[0].strip().lower()
    actual = _sha256_bytes(manifest_bytes)
    if declared != actual:
        return "%s does not match %s (declared %s, actual %s)" % (
            SIDECAR_NAME,
            MANIFEST_NAME,
            declared,
            actual,
        )
    return None


# --------------------------------------------------------------------------- #
# Object enumeration shared by verify and the EXTRA scan
# --------------------------------------------------------------------------- #
def _iter_objects(manifest):
    """Yield (payload_rel_path, sha256, nbytes) for every on-disk payload object.

    Whole files yield one tuple; chunked files yield one tuple per part. Also
    validates each files[].path and the enforced parts[].path layout.
    """
    for entry in manifest["payload"]["files"]:
        fpath = entry.get("path")
        if not isinstance(fpath, str):
            raise UsageError("malformed manifest: file entry without a path")
        _safe_rel(fpath)
        parts = entry.get("parts")
        if parts:
            for part in parts:
                prel = _part_rel(fpath, part)
                yield prel, part.get("sha256"), part.get("bytes")
        else:
            yield fpath, entry.get("sha256"), entry.get("bytes")


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
def _check_object(payload_dir, rel, expected_sha, expected_bytes):
    """Return status string: OK / MISMATCH / MISSING for one payload object."""
    disk = os.path.join(payload_dir, *_safe_rel(rel))
    if not os.path.isfile(disk):
        return "MISSING"
    if os.path.getsize(disk) != expected_bytes or _sha256_file(disk) != expected_sha:
        return "MISMATCH"
    return "OK"


def _verify_verifier(bundle_dir, manifest, report, quiet):
    """Self-check tools/modelferry_offline.py against verifier.sha256."""
    info = manifest.get("verifier") or {}
    rel = info.get("path", DEFAULT_VERIFIER_REL)
    expected = info.get("sha256")
    disk = _safe_join(bundle_dir, rel)
    if not os.path.isfile(disk):
        status = "MISSING"
    elif not isinstance(expected, str) or _sha256_file(disk) != expected:
        status = "MISMATCH"
    else:
        status = "OK"
    report(status, rel, quiet)
    return status == "OK"


def cmd_verify(bundle_dir, quiet):
    manifest, raw = _load_manifest(bundle_dir)
    payload_dir = os.path.join(bundle_dir, PAYLOAD_DIR)
    failures = []

    def report(status, name, quiet_flag):
        if status != "OK":
            failures.append((status, name))
            print("%-8s %s" % (status, name))
        elif not quiet_flag:
            print("%-8s %s" % (status, name))

    side_err = _check_sidecar(bundle_dir, raw)
    if side_err:
        failures.append(("SIDECAR", side_err))
        print("SIDECAR  %s" % side_err)
    elif not quiet:
        print("%-8s %s" % ("OK", SIDECAR_NAME))

    _verify_verifier(bundle_dir, manifest, report, quiet)

    expected_rel = set()
    ok_count = 0
    for rel, sha, nbytes in _iter_objects(manifest):
        expected_rel.add(rel)
        status = _check_object(payload_dir, rel, sha, nbytes)
        report(status, rel, quiet)
        if status == "OK":
            ok_count += 1

    for rel in _scan_extra(payload_dir, expected_rel):
        failures.append(("EXTRA", rel))
        print("EXTRA    %s" % rel)

    if failures:
        print(
            "verify FAILED: %d object(s) OK, %d problem(s) in %s"
            % (ok_count, len(failures), bundle_dir)
        )
        return 1
    print("verify OK: %d object(s) checked in %s" % (ok_count, bundle_dir))
    return 0


def _scan_extra(payload_dir, expected_rel):
    """Yield payload-relative paths present on disk but absent from the manifest."""
    if not os.path.isdir(payload_dir):
        return
    for root, _dirs, files in os.walk(payload_dir):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, payload_dir).replace(os.sep, "/")
            if rel not in expected_rel:
                yield rel


# --------------------------------------------------------------------------- #
# unpack
# --------------------------------------------------------------------------- #
def _dest_ready(dest_dir, force):
    """Ensure the destination is safe to write into (SPEC exit code 4)."""
    if os.path.exists(dest_dir):
        if not os.path.isdir(dest_dir):
            raise LocalFsError("destination %s exists and is not a directory" % dest_dir)
        if os.listdir(dest_dir) and not force:
            raise LocalFsError(
                "destination %s is not empty; pass --force to unpack into it" % dest_dir
            )
    else:
        os.makedirs(dest_dir, exist_ok=True)


def _join_file(entry, payload_dir, dest_dir):
    """Stream-join one manifest file entry into dest_dir; verify whole-file hash."""
    fpath = entry["path"]
    dest = _safe_join(dest_dir, fpath)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    h = hashlib.sha256()
    with open(dest, "wb") as out:
        parts = entry.get("parts")
        if parts:
            for part in parts:
                prel = _part_rel(fpath, part)
                src = _safe_join(payload_dir, prel)
                _open_and_copy(src, out, h)
        else:
            src = _safe_join(payload_dir, fpath)
            _open_and_copy(src, out, h)
    if h.hexdigest() != entry.get("sha256"):
        raise IntegrityError("unpacked %s failed its whole-file hash check" % fpath)


def _open_and_copy(src, out, hasher):
    try:
        fh = open(src, "rb")
    except FileNotFoundError:
        raise IntegrityError("payload object missing: %s" % src) from None
    with fh:
        _copyhash(fh, out, hasher)


def cmd_unpack(bundle_dir, dest_dir, no_verify, force):
    manifest, raw = _load_manifest(bundle_dir)
    _dest_ready(dest_dir, force)
    verified = False
    if not no_verify:
        if cmd_verify(bundle_dir, quiet=True) != 0:
            raise IntegrityError("verify failed; refusing to unpack. Run 'verify' for details.")
        verified = True

    payload_dir = os.path.join(bundle_dir, PAYLOAD_DIR)
    count = 0
    for entry in manifest["payload"]["files"]:
        _join_file(entry, payload_dir, dest_dir)
        count += 1
        print("unpacked %d/%d %s" % (count, len(manifest["payload"]["files"]), entry["path"]))

    _write_receipt(dest_dir, manifest, raw, verified)
    print("unpack OK: %d file(s) into %s (verified=%s)" % (count, dest_dir, verified))
    return 0


def _write_receipt(dest_dir, manifest, raw, verified):
    receipt = {
        "bundle_name": manifest.get("bundle_name"),
        "manifest_sha256": _sha256_bytes(raw),
        "unpacked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verified": verified,
        "verifier_path": (manifest.get("verifier") or {}).get("path", DEFAULT_VERIFIER_REL),
    }
    path = os.path.join(dest_dir, RECEIPT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #
def _human(n):
    if not isinstance(n, int):
        return str(n)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(n)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "%d B" % n
            return "%.1f %s (%d bytes)" % (size, unit, n)
        size /= 1024.0
    return "%d B" % n


def cmd_inspect(bundle_dir):
    manifest, raw = _load_manifest(bundle_dir)
    src = manifest.get("source") or {}
    payload = manifest["payload"]
    tool = manifest.get("tool") or {}
    print("bundle:      %s" % manifest.get("bundle_name"))
    print("repo_id:     %s" % src.get("repo_id"))
    print("commit_sha:  %s" % src.get("commit_sha"))
    print("license:     %s" % src.get("license"))
    print("endpoint:    %s" % src.get("endpoint"))
    print("created_at:  %s" % manifest.get("created_at"))
    print("tool:        %s %s" % (tool.get("name"), tool.get("version")))
    print("files:       %s" % payload.get("file_count"))
    print("total_bytes: %s" % _human(payload.get("total_bytes")))
    print("chunk_size:  %s" % _human(payload.get("chunk_size_bytes")))
    print("manifest_sha256: %s" % _sha256_bytes(raw))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser():
    parser = argparse.ArgumentParser(
        prog="modelferry_offline",
        description="Verify, unpack, or inspect a modelferry bundle offline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="verify a bundle against its manifest")
    p_verify.add_argument("bundle_dir")
    p_verify.add_argument("--quiet", action="store_true", help="print only summary and failures")

    p_unpack = sub.add_parser("unpack", help="verify and reconstruct the model tree")
    p_unpack.add_argument("bundle_dir")
    p_unpack.add_argument("dest_dir")
    p_unpack.add_argument("--no-verify", action="store_true", help="skip the verify pass")
    p_unpack.add_argument("--force", action="store_true", help="unpack into a non-empty directory")

    p_inspect = sub.add_parser("inspect", help="print a bundle summary (no hashing)")
    p_inspect.add_argument("bundle_dir")

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            return cmd_verify(args.bundle_dir, args.quiet)
        if args.command == "unpack":
            return cmd_unpack(args.bundle_dir, args.dest_dir, args.no_verify, args.force)
        if args.command == "inspect":
            return cmd_inspect(args.bundle_dir)
        parser.error("unknown command")  # argparse exits 2
    except MferryError as e:
        print("error: %s" % e, file=sys.stderr)
        return e.exit_code
    except OSError as e:
        print("error: local filesystem error: %s" % e, file=sys.stderr)
        return LocalFsError.exit_code


if __name__ == "__main__":
    sys.exit(main())

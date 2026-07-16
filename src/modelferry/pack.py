"""Connected-side pack orchestration: chunk, hash, write, self-verify (§3, §8).

The bundle writer (write_bundle) works from a local directory of files and needs
no network, so it is unit-testable offline and drives the §11 writer/reader
round-trip. hf.py supplies the snapshot directory and source metadata.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import platform
import posixpath
import re
import shutil
from datetime import datetime, timezone

from . import __version__, manifest, offline
from .errors import LocalFsError, UsageError

BUF_SIZE = 8 * 1024 * 1024
MAX_PARTS = 9999  # part index is exactly four digits; refuse anything that needs more
TMP_SUFFIX = ".mftmp"  # reserved by the unpack side; a repo path must not end in it
_UNIT = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_chunk_size(text):
    """Parse '3900M' / '16G' / 'none' into a byte count or None. Raises UsageError."""
    if text is None or text.strip().lower() == "none":
        return None
    s = text.strip()
    mult = 1
    if s and s[-1].upper() in _UNIT:
        mult = _UNIT[s[-1].upper()]
        s = s[:-1]
    try:
        value = int(float(s) * mult)
    except ValueError:
        raise UsageError(f"invalid --chunk-size {text!r}; use e.g. 3900M, 16G, or none") from None
    if value <= 0:
        raise UsageError(f"invalid --chunk-size {text!r}; must be positive")
    return value


def slugify(repo_id):
    name = repo_id.split("/")[-1].lower()
    return re.sub(r"[^a-z0-9._-]+", "-", name).strip("-") or "bundle"


def _part_count(size, chunk_size):
    if not chunk_size or size <= chunk_size:
        return 0
    return (size + chunk_size - 1) // chunk_size


def _part_paths(rel, count):
    parent = posixpath.dirname(rel)
    base = posixpath.basename(rel)
    for idx in range(count):
        name = f"{base}.mfpart{idx:04d}"
        yield posixpath.join(parent, name) if parent else name


def _check_repo_path(rel):
    """Reject a repo path the offline reader's _safe_rel would reject (exit 2).

    Mirrors offline.py _safe_rel: backslash, absolute or drive-lettered paths, and
    any empty, '.' or '..' segment. offline.py stays the source of truth; this is
    the pack-side early guard so an unusable path fails before the download instead
    of at the post-write self-verify, after the whole repo is on disk. Parts are
    generated from a validated path, so validating the file path covers them.
    """
    if not isinstance(rel, str) or not rel:
        raise UsageError("repo path is empty or not a string")
    if "\\" in rel:
        raise UsageError(f"repo path {rel!r} contains a backslash; exclude it and re-pack")
    if rel.startswith("/") or ntpath.splitdrive(rel)[0] or ":" in rel:
        raise UsageError(f"repo path {rel!r} is absolute or drive-lettered; exclude it and re-pack")
    for seg in rel.split("/"):
        if seg in ("", ".", ".."):
            raise UsageError(
                f"repo path {rel!r} has an illegal '.', '..', or empty segment; "
                "exclude it and re-pack"
            )


def preflight(files, chunk_size):
    """Reject a payload layout before any bytes are written (exit 2).

    files is a list of (repo_rel_path, size_bytes). Refuses a path the offline
    reader would reject, two colliding payload paths, a repo path ending in the
    reserved .mftmp suffix, or any file needing more than MAX_PARTS parts; the
    message names the offending file (and the minimum viable chunk size for the
    part-count case).
    """
    seen = {}
    for rel, size in files:
        _check_repo_path(rel)
        if rel.endswith(TMP_SUFFIX):
            raise UsageError(
                f"repo path {rel!r} ends in the reserved suffix {TMP_SUFFIX}; "
                "exclude it and re-pack"
            )
        count = _part_count(size, chunk_size)
        if count > MAX_PARTS:
            min_chunk = (size + MAX_PARTS - 1) // MAX_PARTS
            raise UsageError(
                f"file {rel!r} needs {count} parts at this chunk size (limit {MAX_PARTS}). "
                f"Use a chunk size of at least {min_chunk} bytes."
            )
        payload_paths = [rel] if count == 0 else list(_part_paths(rel, count))
        for pp in payload_paths:
            if pp in seen:
                raise UsageError(
                    f"payload path collision: {pp!r} is claimed by both {seen[pp]!r} and {rel!r}"
                )
            seen[pp] = rel


def _copy_whole(src, dest):
    """Stream a whole file into dest, returning (bytes, sha256). Fixed buffer."""
    h = hashlib.sha256()
    total = 0
    with open(src, "rb") as fi, open(dest, "wb") as fo:
        while True:
            buf = fi.read(BUF_SIZE)
            if not buf:
                break
            fo.write(buf)
            h.update(buf)
            total += len(buf)
    return total, h.hexdigest()


def _split_into_parts(src, rel, payload_dir, chunk_size):
    """Single-pass split: read src once, feed the whole-file hash and per-part
    writers/hashes, rolling parts at the chunk boundary (§8). Returns (parts,
    whole_sha, total_bytes)."""
    whole = hashlib.sha256()
    parts = []
    idx = 0

    def _open(i):
        prel = _part_rel(rel, i)
        disk = os.path.join(payload_dir, *prel.split("/"))
        os.makedirs(os.path.dirname(disk), exist_ok=True)
        return prel, disk, open(disk, "wb"), hashlib.sha256(), 0

    prel, disk, fo, ph, written = _open(idx)
    total = 0
    try:
        with open(src, "rb") as fi:
            while True:
                buf = fi.read(BUF_SIZE)
                if not buf:
                    break
                whole.update(buf)
                total += len(buf)
                view = memoryview(buf)
                off = 0
                while off < len(buf):
                    take = min(chunk_size - written, len(buf) - off)
                    seg = view[off : off + take]
                    fo.write(seg)
                    ph.update(seg)
                    written += take
                    off += take
                    if written == chunk_size:
                        fo.close()
                        parts.append(_part_entry(prel, written, ph))
                        idx += 1
                        prel, disk, fo, ph, written = _open(idx)
        fo.close()
        if written > 0 or not parts:
            parts.append(_part_entry(prel, written, ph))
        else:
            os.remove(disk)  # exact multiple: drop the empty trailing part
    finally:
        if not fo.closed:
            fo.close()
    return parts, whole.hexdigest(), total


def _part_rel(rel, idx):
    parent = posixpath.dirname(rel)
    name = f"{posixpath.basename(rel)}.mfpart{idx:04d}"
    return posixpath.join(parent, name) if parent else name


def _part_entry(prel, nbytes, hasher):
    return {
        "name": posixpath.basename(prel),
        "path": prel,
        "bytes": nbytes,
        "sha256": hasher.hexdigest(),
    }


def _write_one(src, rel, payload_dir, chunk_size):
    size = os.path.getsize(src)
    if not chunk_size or size <= chunk_size:
        disk = os.path.join(payload_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(disk) or payload_dir, exist_ok=True)
        nbytes, sha = _copy_whole(src, disk)
        return {"path": rel, "bytes": nbytes, "sha256": sha}
    parts, whole_sha, nbytes = _split_into_parts(src, rel, payload_dir, chunk_size)
    return {"path": rel, "bytes": nbytes, "sha256": whole_sha, "parts": parts}


def _install_verifier(bundle_dir):
    """Copy this package's offline.py verbatim into the bundle; return (rel, sha)."""
    source = os.path.join(os.path.dirname(__file__), "offline.py")
    tools = os.path.join(bundle_dir, "tools")
    os.makedirs(tools, exist_ok=True)
    dest = os.path.join(tools, "modelferry_offline.py")
    shutil.copyfile(source, dest)
    with open(dest, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return "tools/modelferry_offline.py", sha


def _tool_block():
    return {
        "name": "modelferry",
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def write_bundle(
    snapshot_dir, rel_files, dest_root, chunk_size, source, created_at=None, tool=None
):
    """Write a bundle from a local snapshot directory. Returns the bundle path.

    Runs the pre-flight payload check first, streams every file into payload/,
    copies the verifier in, writes manifest.json / manifest.sha256 / MANIFEST.md,
    then runs the offline verifier against the result (read-back self-check, §8).
    """
    rel_files = sorted(rel_files)
    sized = [
        (rel, os.path.getsize(os.path.join(snapshot_dir, *rel.split("/")))) for rel in rel_files
    ]
    preflight(sized, chunk_size)

    bundle_name = f"{slugify(source['repo_id'])}__{source['commit_sha'][:7]}"
    bundle_dir = os.path.join(dest_root, bundle_name)
    if os.path.isdir(bundle_dir) and os.listdir(bundle_dir):
        raise LocalFsError(
            f"bundle directory already exists and is not empty: {bundle_dir}. "
            "Remove it or choose a different --dest, then re-run."
        )
    payload_dir = os.path.join(bundle_dir, "payload")
    os.makedirs(payload_dir, exist_ok=True)

    entries = []
    for i, rel in enumerate(rel_files, 1):
        print(f"packing {i}/{len(rel_files)} {rel}")
        entries.append(
            _write_one(os.path.join(snapshot_dir, *rel.split("/")), rel, payload_dir, chunk_size)
        )

    verifier_rel, verifier_sha = _install_verifier(bundle_dir)
    man = manifest.build_manifest(
        bundle_name=bundle_name,
        created_at=created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tool=tool or _tool_block(),
        source=source,
        chunk_size_bytes=chunk_size,
        files=entries,
        verifier={"path": verifier_rel, "sha256": verifier_sha},
    )
    raw = manifest.serialize(man)
    with open(os.path.join(bundle_dir, "manifest.json"), "wb") as f:
        f.write(raw)
    with open(os.path.join(bundle_dir, "manifest.sha256"), "w", encoding="utf-8") as f:
        f.write(manifest.sidecar_text(raw))
    manifest_sha = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(bundle_dir, "MANIFEST.md"), "w", encoding="utf-8") as f:
        f.write(manifest.render_manifest_md(man, manifest_sha))

    if offline.cmd_verify(bundle_dir, quiet=True) != 0:
        raise LocalFsError(
            f"post-pack self-verify failed for {bundle_dir}; the bundle may be corrupt (disk "
            "error or full disk). Re-run pack."
        )
    return bundle_dir


def pack(
    repo_id, dest, revision="main", chunk_size="3900M", include=None, exclude=None, staging=None
):
    """Resolve, download, and pack a Hugging Face model repo. Returns the bundle path."""
    from . import hf  # imported here so offline-only tests never import huggingface_hub

    chunk_bytes = parse_chunk_size(chunk_size)
    snapshot_dir, source, rel_files = hf.resolve_and_download(
        repo_id, revision, staging, include, exclude
    )
    os.makedirs(dest, exist_ok=True)
    return write_bundle(snapshot_dir, rel_files, dest, chunk_bytes, source)

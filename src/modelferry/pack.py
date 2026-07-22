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
import tempfile
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


def _make_signer(sign):
    """Resolve the signer for a pack; None when unsigned.

    Signing is opt-in (sign=True) and additionally requires MODELFERRY_SIGNING_KEY
    to be configured. Asking to sign without a key is a usage error (exit 2), never
    a silent unsigned pack. Building the signer here, before the download in pack(),
    fails fast on a missing or unreadable key.
    """
    if not sign:
        return None
    from .signing import Ed25519Signer, SigningError

    try:
        return Ed25519Signer()
    except SigningError as e:
        raise UsageError(f"--sign was requested but signing is not configured: {e}") from None


def write_bundle(
    snapshot_dir, rel_files, dest_root, chunk_size, source, created_at=None, tool=None, signer=None
):
    """Write a bundle from a local snapshot directory. Returns the bundle path.

    Runs the pre-flight payload check first, streams every file into payload/,
    copies the verifier in, writes manifest.json / manifest.sha256 / MANIFEST.md,
    then runs the offline verifier against the result (read-back self-check, §8).

    signer, when given (see _make_signer), makes this a signed bundle: the manifest
    is built with a signing block, the exact serialized bytes are signed, and the
    detached signature is written to manifest.SIGNATURE_FILENAME. Signing is
    additive; with signer=None the bundle is byte-for-byte what it was before.
    """
    rel_files = sorted(rel_files)
    sized = [
        (rel, os.path.getsize(os.path.join(snapshot_dir, *rel.split("/")))) for rel in rel_files
    ]
    preflight(sized, chunk_size)

    bundle_name = _bundle_name(source["repo_id"], source["commit_sha"])
    bundle_dir = os.path.join(dest_root, bundle_name)
    _check_bundle_path(dest_root, source["repo_id"], source["commit_sha"])  # cheap re-check
    payload_dir = os.path.join(bundle_dir, "payload")
    os.makedirs(payload_dir, exist_ok=True)

    entries = []
    for i, rel in enumerate(rel_files, 1):
        print(f"packing {i}/{len(rel_files)} {rel}")
        entries.append(
            _write_one(os.path.join(snapshot_dir, *rel.split("/")), rel, payload_dir, chunk_size)
        )

    verifier_rel, verifier_sha = _install_verifier(bundle_dir)
    # The signing block must be present before serialization, because the signature
    # covers the final manifest bytes including that block (SPEC §5).
    signing_block = manifest.signing_block(key_id=signer.key_id()) if signer is not None else None
    man = manifest.build_manifest(
        bundle_name=bundle_name,
        created_at=created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tool=tool or _tool_block(),
        source=source,
        chunk_size_bytes=chunk_size,
        files=entries,
        verifier={"path": verifier_rel, "sha256": verifier_sha},
        signing=signing_block,
    )
    raw = manifest.serialize(man)
    with open(os.path.join(bundle_dir, "manifest.json"), "wb") as f:
        f.write(raw)
    with open(os.path.join(bundle_dir, "manifest.sha256"), "w", encoding="utf-8") as f:
        f.write(manifest.sidecar_text(raw))
    manifest_sha = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(bundle_dir, "MANIFEST.md"), "w", encoding="utf-8") as f:
        f.write(manifest.render_manifest_md(man, manifest_sha))

    # Sign the exact serialized manifest bytes and write the detached sidecar. The
    # manifest never contains its own signature; the sidecar does (SPEC §5).
    if signer is not None:
        with open(os.path.join(bundle_dir, manifest.SIGNATURE_FILENAME), "wb") as f:
            f.write(signer.sign(raw))

    if offline.cmd_verify(bundle_dir, quiet=True) != 0:
        raise LocalFsError(
            f"post-pack self-verify failed for {bundle_dir}; the bundle may be corrupt (disk "
            "error or full disk). Re-run pack."
        )
    # Signed bundles also self-check the signature: prove it verifies against our
    # own public key before shipping, mirroring the integrity self-verify above.
    if signer is not None:
        from . import verify_signature

        result = verify_signature.verify_bundle_signature(bundle_dir, signer.public_key_bytes)
        if result.outcome != verify_signature.VALID:
            raise LocalFsError(
                f"post-pack signature self-check failed for {bundle_dir}: {result.message} "
                "Re-run pack."
            )
    return bundle_dir


def _validate_dest(dest):
    """Ensure --dest exists as a writable directory before any download (exit 4).

    Creates it if absent, confirms it is a directory, then probes writability by
    creating and deleting a temp file. --dest is usually removable media, so an
    unmounted stick or a wrong drive letter is caught here, up front, instead of
    after a multi-gigabyte download that then fails on the very last step.
    """
    if not os.path.isdir(dest):
        if os.path.exists(dest):
            raise LocalFsError(
                f"--dest {dest} exists but is not a directory. Choose a directory, then re-run."
            )
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            raise LocalFsError(
                f"cannot create --dest {dest}: {e}. Check the path (is the drive or media "
                "mounted?) and permissions, then re-run."
            ) from None
    try:
        fd, probe = tempfile.mkstemp(prefix=".mfwrite-", dir=dest)
        os.close(fd)
        os.remove(probe)
    except OSError as e:
        raise LocalFsError(
            f"--dest {dest} is not writable: {e}. Check permissions or choose a different "
            "--dest, then re-run."
        ) from None
    return dest


def _existing_ancestor(path):
    """Nearest existing directory at or above path (path itself may not exist yet)."""
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def _free_bytes(path):
    return shutil.disk_usage(_existing_ancestor(path)).free


def _same_volume(a, b):
    return os.stat(_existing_ancestor(a)).st_dev == os.stat(_existing_ancestor(b)).st_dev


def _check_free_space(total_bytes, staging_dir, dest_root):
    """Refuse before downloading if free space is short (exit 4).

    The download lands in staging_dir and the bundle is written under dest_root;
    each holds roughly total_bytes. When they share a volume both live there at
    once, so that volume needs about twice total_bytes; check the combined
    requirement rather than each path in isolation. Sizes come from hub metadata
    (§3); if the hub reports no sizes total_bytes is 0 and the check is a no-op.
    """
    if _same_volume(staging_dir, dest_root):
        need = 2 * total_bytes
        free = _free_bytes(dest_root)
        if free < need:
            raise LocalFsError(
                f"not enough free space on the volume holding both --staging and --dest "
                f"{dest_root}: need about {need} bytes (twice the {total_bytes}-byte "
                f"download, since they share a volume), {free} available. Free space, or "
                "put --staging and --dest on different volumes, then re-run."
            )
        return
    staging_free = _free_bytes(staging_dir)
    if staging_free < total_bytes:
        raise LocalFsError(
            f"not enough free space for the download in the staging directory "
            f"{staging_dir}: need {total_bytes} bytes, {staging_free} available. Free "
            "space or choose a different --staging, then re-run."
        )
    dest_free = _free_bytes(dest_root)
    if dest_free < total_bytes:
        raise LocalFsError(
            f"not enough free space for the bundle at --dest {dest_root}: need about "
            f"{total_bytes} bytes, {dest_free} available. Free space or choose a "
            "different --dest, then re-run."
        )


def _bundle_name(repo_id, commit_sha):
    return f"{slugify(repo_id)}__{commit_sha[:7]}"


def _check_bundle_path(dest_root, repo_id, commit_sha):
    """Refuse before downloading if the target bundle directory already has
    contents (exit 4).

    The bundle name is slug + 7-char sha, both known from the resolved commit, so
    this is checkable before any bytes move. Same class of failure as an unmounted
    --dest: fail in a second instead of after the whole download. write_bundle
    re-checks at write time as a cheap assertion.
    """
    bundle_dir = os.path.join(dest_root, _bundle_name(repo_id, commit_sha))
    if os.path.isdir(bundle_dir) and os.listdir(bundle_dir):
        raise LocalFsError(
            f"bundle directory already exists and is not empty: {bundle_dir}. "
            "Remove it or choose a different --dest, then re-run."
        )


def pack(
    repo_id,
    dest,
    revision="main",
    chunk_size="3900M",
    include=None,
    exclude=None,
    staging=None,
    sign=False,
):
    """Resolve, download, and pack a Hugging Face model repo. Returns the bundle path.

    --dest, free space, and the target bundle path are all validated before the
    download (§3). dest is the likeliest thing to be wrong (unmounted media, wrong
    drive letter), and the bundle name is known from the resolved commit, so each
    is checked up front instead of after the whole repo is on disk. sign=True builds
    the signer up front too (exit 2 if no key), so a missing key fails before the
    download, not after.
    """
    from . import hf  # imported here so offline-only tests never import huggingface_hub

    chunk_bytes = parse_chunk_size(chunk_size)
    dest_root = _validate_dest(dest)
    signer = _make_signer(sign)  # fail fast (exit 2) before any download
    resolved = hf.resolve(repo_id, revision, staging, include, exclude)
    _check_free_space(resolved["total_bytes"], resolved["local_dir"], dest_root)
    _check_bundle_path(dest_root, repo_id, resolved["commit_sha"])
    wanted = [rel for rel, _ in resolved["files"]]
    snapshot_dir, rel_files = hf.download(
        repo_id,
        resolved["commit_sha"],
        resolved["local_dir"],
        resolved["endpoint"],
        include,
        exclude,
        wanted,
    )
    return write_bundle(
        snapshot_dir, rel_files, dest_root, chunk_bytes, resolved["source"], signer=signer
    )

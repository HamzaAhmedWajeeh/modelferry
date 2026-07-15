"""Hand-built fixture-bundle helpers for the offline verifier tests.

Pack-side code (hf.py/pack.py/manifest.py) does not exist yet, so these helpers
stand in as the "writer": they build bundles that offline.py must read, using the
same on-disk layout and deterministic manifest serialization the real pack side
will use. Importing any modelferry package code here is deliberately avoided;
offline.py is exercised as a subprocess (see run_offline).
"""

import hashlib
import json
import posixpath
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OFFLINE_PY = REPO_ROOT / "src" / "modelferry" / "offline.py"

_BUF = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_BUF), b""):
            h.update(chunk)
    return h.hexdigest()


def deterministic_bytes(n: int) -> bytes:
    """A reproducible byte pattern of length n (no randomness; resume-safe)."""
    return bytes((i * 37 + 11) % 256 for i in range(n))


def run_offline(args):
    """Run the offline verifier as a subprocess. Returns (returncode, out, err)."""
    cmd = [sys.executable, str(OFFLINE_PY)] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _write_payload_file(bundle_dir: Path, rel: str, data: bytes) -> None:
    disk = bundle_dir / "payload"
    for seg in rel.split("/"):
        disk = disk / seg
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(data)


def _install_verifier(bundle_dir: Path):
    tools = bundle_dir / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    dst = tools / "modelferry_offline.py"
    shutil.copyfile(OFFLINE_PY, dst)
    return "tools/modelferry_offline.py", sha256_file(dst)


def finalize_manifest(
    bundle_dir, manifest, payload_files=None, fix_verifier=True, fix_sidecar=True
):
    """Write a bundle from a fully-formed manifest dict plus payload bytes.

    Copies the real offline.py into tools/ and records its sha256 into
    manifest['verifier'] (unless fix_verifier is False), serializes manifest.json
    deterministically, and writes the matching manifest.sha256 sidecar.
    """
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in (payload_files or {}).items():
        _write_payload_file(bundle_dir, rel, data)
    if fix_verifier:
        rel, sha = _install_verifier(bundle_dir)
        manifest.setdefault("verifier", {})
        manifest["verifier"]["path"] = rel
        manifest["verifier"]["sha256"] = sha
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (bundle_dir / "manifest.json").write_bytes(raw)
    if fix_sidecar:
        sidecar = f"{sha256_bytes(raw)}  manifest.json\n"
        (bundle_dir / "manifest.sha256").write_text(sidecar, encoding="utf-8")
    return bundle_dir


def _base_manifest(files_entries, total, chunk_size):
    return {
        "manifest_version": 1,
        "bundle_name": "fixture__0000000",
        "created_at": "2026-07-15T09:30:00Z",
        "tool": {"name": "modelferry", "version": "0.1.0", "python": "3.12.0", "platform": "test"},
        "source": {
            "type": "huggingface",
            "endpoint": "https://huggingface.co",
            "repo_id": "acme/fixture",
            "repo_type": "model",
            "revision_requested": "main",
            "commit_sha": "0" * 40,
            "license": "apache-2.0",
            "gated": False,
        },
        "payload": {
            "hash_algorithm": "sha256",
            "chunk_size_bytes": int(chunk_size or 0),
            "file_count": len(files_entries),
            "total_bytes": total,
            "files": files_entries,
        },
    }


def build_bundle(bundle_dir, files, chunk_size=None):
    """Build a valid bundle.

    files: {repo-relative POSIX path: bytes}. Files strictly larger than
    chunk_size are split into .mfpartNNNN parts written next to the parent file
    (payload/<dir>/<base>.mfpartNNNN), matching the SPEC section 4/5 layout.
    """
    file_entries = []
    payload_files = {}
    total = 0
    for path in sorted(files):
        data = files[path]
        total += len(data)
        entry = {"path": path, "bytes": len(data), "sha256": sha256_bytes(data)}
        if chunk_size and len(data) > chunk_size:
            parts = []
            parent = posixpath.dirname(path)
            base = posixpath.basename(path)
            offset = 0
            idx = 0
            while offset < len(data):
                chunk = data[offset : offset + chunk_size]
                name = f"{base}.mfpart{idx:04d}"
                ppath = posixpath.join(parent, name) if parent else name
                payload_files[ppath] = chunk
                parts.append(
                    {
                        "name": name,
                        "path": ppath,
                        "bytes": len(chunk),
                        "sha256": sha256_bytes(chunk),
                    }
                )
                offset += chunk_size
                idx += 1
            entry["parts"] = parts
        else:
            payload_files[path] = data
        file_entries.append(entry)
    manifest = _base_manifest(file_entries, total, chunk_size)
    return finalize_manifest(bundle_dir, manifest, payload_files)

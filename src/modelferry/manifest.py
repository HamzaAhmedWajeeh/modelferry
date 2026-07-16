"""Manifest construction and MANIFEST.md rendering (pack side only).

The reader in offline.py has its own independent manifest parser; the §11
writer/reader round-trip test keeps the two honest. Serialization here is
deterministic: json.dumps(indent=2, sort_keys=True) plus a trailing newline, so
the same inputs always produce byte-identical manifest.json.
"""

from __future__ import annotations

import hashlib
import json

MANIFEST_VERSION = 1


def build_manifest(bundle_name, created_at, tool, source, chunk_size_bytes, files, verifier):
    """Assemble the manifest dict. Pure function of its inputs (see §5).

    files is a list of file entries already carrying path/bytes/sha256 (and parts
    for chunked files). total_bytes and file_count are derived here.
    """
    ordered = sorted(files, key=lambda f: f["path"])
    total_bytes = sum(f["bytes"] for f in ordered)
    return {
        "manifest_version": MANIFEST_VERSION,
        "bundle_name": bundle_name,
        "created_at": created_at,
        "tool": tool,
        "source": source,
        "payload": {
            "hash_algorithm": "sha256",
            "chunk_size_bytes": int(chunk_size_bytes or 0),
            "file_count": len(ordered),
            "total_bytes": total_bytes,
            "files": ordered,
        },
        "verifier": verifier,
    }


def serialize(manifest):
    """Return the canonical manifest.json bytes."""
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sidecar_text(manifest_bytes):
    """Return the manifest.sha256 sidecar line, sha256sum format (two spaces)."""
    return f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n"


def human_bytes(n):
    """Human-readable size with the exact byte count, e.g. '3.8 GiB (4089446400 bytes)'."""
    if not isinstance(n, int):
        return str(n)
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{n} B"
            return f"{size:.1f} {unit} ({n} bytes)"
        size /= 1024.0
    return f"{n} B"


def _md_cell(text):
    """Escape a value for a Markdown table cell so a '|' in it can't add columns."""
    return str(text).replace("\\", "\\\\").replace("|", "\\|")


def _object_count(entry):
    """Number of payload objects on media for one file: 1 whole, else one per part.

    Matches how offline.py verify counts objects, so the MANIFEST.md Parts column
    sums to what verify reports.
    """
    parts = entry.get("parts")
    return len(parts) if parts else 1


def render_manifest_md(manifest, manifest_sha256):
    """Render the officer-facing MANIFEST.md per §6. Plain prose, no marketing."""
    src = manifest["source"]
    payload = manifest["payload"]
    tool = manifest["tool"]
    verifier = manifest.get("verifier") or {}
    chunk_bytes = payload.get("chunk_size_bytes") or 0
    chunk_display = "none" if chunk_bytes == 0 else human_bytes(chunk_bytes)

    files = payload["files"]
    file_count = payload.get("file_count")
    if file_count is None:
        file_count = len(files)
    object_count = sum(_object_count(e) for e in files)

    gated = "yes" if src.get("gated") else "no"
    lines = [
        f"# modelferry bundle: {manifest.get('bundle_name')}",
        "",
    ]
    if src.get("license") == "UNKNOWN":
        lines += [
            "> WARNING: the license for this model could not be determined from repo",
            "> metadata. Confirm the license terms before using or redistributing this",
            "> model.",
            "",
        ]
    lines += [
        "This document is the approval record for this bundle. It gets used twice.",
        "",
        "Before transfer: review the details below and approve them, then keep a copy of",
        "this file. The manifest checksum in your copy is what the receiving side checks",
        "against.",
        "",
        "On arrival: run the commands in the Verify section. If the checksum inspect",
        "prints differs from the one in your approved copy, or if verify reports anything",
        "other than OK, this is not the bundle that was approved. Do not use it.",
        "",
        "## Source",
        "",
        f"- Repo: {src.get('repo_id')}",
        f"- Commit: {src.get('commit_sha')}",
        f"- Revision requested: {src.get('revision_requested')}",
        f"- License: {src.get('license')}",
        f"- Gated: {gated}",
        f"- Endpoint: {src.get('endpoint')}",
        f"- Created: {manifest.get('created_at')}",
        f"- Tool: {tool.get('name')} {tool.get('version')}",
        "",
        "## Totals",
        "",
        f"- Files: {file_count}",
        f"- Payload objects on media: {object_count}",
        f"- Total size: {human_bytes(payload.get('total_bytes'))}",
        f"- Chunk size: {chunk_display}",
        "",
    ]
    if object_count > file_count:
        lines += [
            "Files larger than the chunk size are split into parts, so the media holds more",
            "objects than the model has files. The Parts column below shows the split.",
            "",
        ]
    lines += [
        "## Manifest checksum",
        "",
        "The sha256 of manifest.json is:",
        "",
        f"    {manifest_sha256}",
        "",
        "This is the checksum your approved copy carries. The Verify section explains how",
        "the receiving side checks the arrived bundle against it.",
        "",
        "## Verifier",
        "",
        f"The bundled verifier is {verifier.get('path')}. Its sha256 is:",
        "",
        f"    {verifier.get('sha256')}",
        "",
        'This hash is also recorded in manifest.json under "verifier". Compare it to the',
        "canonical hash published with the modelferry release, or bring your own copy of",
        "the verifier, to check the verifier out-of-band (see the trust model in the",
        "README).",
        "",
        "## Verify",
        "",
        "Run these on the receiving side. They need Python 3.9 or newer, no network and",
        "no packages.",
        "",
        "    cd <bundle directory>",
        "    python3 tools/modelferry_offline.py inspect .",
        "    python3 tools/modelferry_offline.py verify .",
        "",
        "inspect recomputes the manifest checksum from the file on disk and prints it as",
        "manifest_sha256. Compare that to the checksum in the approved copy of this",
        "document. verify then checks every object on the media against the manifest and",
        'prints "verify OK" only if all of them match.',
        "",
        "## Files",
        "",
        "| Path | Size (bytes) | Parts | sha256 |",
        "| --- | --- | --- | --- |",
    ]
    for entry in files:
        lines.append(
            f"| {_md_cell(entry['path'])} | {entry['bytes']} | "
            f"{_object_count(entry)} | {entry['sha256']} |"
        )
    lines.append("")
    return "\n".join(lines)

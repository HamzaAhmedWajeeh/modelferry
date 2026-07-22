"""Network integration test (SPEC section 11): pack the tiny public repo, verify,
unpack, and byte-compare the unpacked tree with the staging snapshot.

Marked network so the default offline test run skips it; CI runs it in its own job.
"""

from pathlib import Path

import pytest

from _bundle import run_offline
from modelferry import offline

TINY_REPO = "hf-internal-testing/tiny-random-gpt2"

# Task 0.3: manifest is v2 but offline.py (pack's post-pack self-verify) accepts
# only v1 until task 0.4, so pack.pack raises offline.UsageError. strict xfail;
# remove when 0.4 lands. See test_pack_writer.needs_v2_reader for the rationale.
needs_v2_reader = pytest.mark.xfail(
    reason="TODO(0.4): offline accepts v2",
    raises=offline.UsageError,
    strict=True,
)


@pytest.mark.network
@needs_v2_reader
def test_pack_verify_unpack_tiny_repo(tmp_path):
    from modelferry import hf, pack

    staging = str(tmp_path / "staging")
    # Production path: resolve then download (exactly what pack() calls), to get
    # the snapshot we compare the unpacked tree against.
    resolved = hf.resolve(TINY_REPO, "main", staging, None, None)
    wanted = [rel for rel, _ in resolved["files"]]
    snapshot_dir, rel_files = hf.download(
        TINY_REPO,
        resolved["commit_sha"],
        resolved["local_dir"],
        resolved["endpoint"],
        None,
        None,
        wanted,
    )
    assert rel_files, "expected the tiny repo to contain files"

    # Full pack orchestration (re-resolves, reuses the cached download). 1M forces chunking.
    bundle = pack.pack(TINY_REPO, str(tmp_path / "bundles"), chunk_size="1M", staging=staging)

    code, out, err = run_offline(["verify", bundle])
    assert code == 0, err + out

    unpacked = tmp_path / "unpacked"
    code, out, err = run_offline(["unpack", bundle, str(unpacked)])
    assert code == 0, err + out

    for rel in rel_files:
        original = Path(snapshot_dir).joinpath(*rel.split("/")).read_bytes()
        assert (unpacked / Path(rel)).read_bytes() == original

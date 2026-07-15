"""Network integration test (SPEC section 11): pack the tiny public repo, verify,
unpack, and byte-compare the unpacked tree with the staging snapshot.

Marked network so the default offline test run skips it; CI runs it in its own job.
"""

from pathlib import Path

import pytest

from _bundle import run_offline

TINY_REPO = "hf-internal-testing/tiny-random-gpt2"


@pytest.mark.network
def test_pack_verify_unpack_tiny_repo(tmp_path):
    from modelferry import hf, pack

    staging = str(tmp_path / "staging")
    # Resolve + download once to get the snapshot we will compare against.
    snapshot_dir, _source, rel_files = hf.resolve_and_download(
        TINY_REPO, "main", staging, None, None
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

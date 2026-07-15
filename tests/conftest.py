"""Fixtures for the offline verifier tests. Thin wrappers over tests/_bundle.py."""

import pytest

import _bundle


@pytest.fixture
def build_bundle():
    """Return the bundle builder: build_bundle(dir, {path: bytes}, chunk_size=None)."""
    return _bundle.build_bundle


@pytest.fixture
def finalize_manifest():
    """Return the low-level writer for hand-crafted manifests."""
    return _bundle.finalize_manifest


@pytest.fixture
def run_offline():
    """Return run_offline(args) -> (returncode, stdout, stderr)."""
    return _bundle.run_offline

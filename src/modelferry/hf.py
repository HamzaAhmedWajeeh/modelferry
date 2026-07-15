"""Hugging Face hub access: resolve a revision to a commit, download at that
commit, and report source metadata (license, gated, endpoint) for the manifest.

HF_TOKEN is read from the environment only and is never returned, logged, or
written anywhere (§9). Hub failures map to SPEC exit code 3 (source error).
"""

from __future__ import annotations

import fnmatch
import os

from .errors import SourceError

DEFAULT_ENDPOINT = "https://huggingface.co"


def _select(candidates, include, exclude):
    """Apply fnmatch include/exclude against repo-relative paths. Exclude wins."""
    selected = []
    for path in candidates:
        if include and not any(fnmatch.fnmatch(path, pat) for pat in include):
            continue
        if exclude and any(fnmatch.fnmatch(path, pat) for pat in exclude):
            continue
        selected.append(path)
    return selected


def _extract_license(info):
    card = getattr(info, "card_data", None)
    if card is not None:
        value = card.get("license") if hasattr(card, "get") else getattr(card, "license", None)
        if value:
            return str(value)
    for tag in getattr(info, "tags", None) or []:
        if isinstance(tag, str) and tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return "UNKNOWN"


def resolve_and_download(repo_id, revision, staging, include, exclude):
    """Return (snapshot_dir, source_metadata, rel_files). Raises SourceError."""
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.utils import (
        GatedRepoError,
        HfHubHTTPError,
        RepositoryNotFoundError,
    )

    token = os.environ.get("HF_TOKEN")
    endpoint = os.environ.get("HF_ENDPOINT", DEFAULT_ENDPOINT)
    staging = staging or os.path.join(os.path.expanduser("~"), ".cache", "modelferry")
    api = HfApi(endpoint=endpoint, token=token)

    try:
        info = api.repo_info(repo_id, revision=revision, repo_type="model", files_metadata=True)
    except GatedRepoError as e:
        raise SourceError(
            f"repo {repo_id!r} is gated; set HF_TOKEN to an account with access ({e})"
        ) from None
    except RepositoryNotFoundError as e:
        raise SourceError(
            f"repo {repo_id!r} not found (or private without a valid HF_TOKEN): {e}"
        ) from None
    except (HfHubHTTPError, OSError, ValueError) as e:
        raise SourceError(f"could not read repo {repo_id!r} from the hub: {e}") from None

    commit_sha = getattr(info, "sha", None)
    if not commit_sha:
        raise SourceError(f"hub did not return a commit sha for {repo_id!r} at {revision!r}")

    siblings = [s.rfilename for s in (getattr(info, "siblings", None) or [])]
    wanted = _select(siblings, include, exclude)
    if not wanted:
        raise SourceError(f"no files matched after --include/--exclude for {repo_id!r}")

    try:
        snapshot_dir = snapshot_download(
            repo_id,
            repo_type="model",
            revision=commit_sha,
            cache_dir=staging,
            allow_patterns=include or None,
            ignore_patterns=exclude or None,
            token=token,
            endpoint=endpoint,
        )
    except (HfHubHTTPError, OSError, ValueError) as e:
        raise SourceError(f"download of {repo_id!r} failed: {e}") from None

    rel_files = [
        rel for rel in wanted if os.path.isfile(os.path.join(snapshot_dir, *rel.split("/")))
    ]
    if not rel_files:
        raise SourceError(f"nothing was downloaded for {repo_id!r}")

    gated_raw = getattr(info, "gated", False)
    source = {
        "type": "huggingface",
        "endpoint": endpoint,
        "repo_id": repo_id,
        "repo_type": "model",
        "revision_requested": revision,
        "commit_sha": commit_sha,
        "license": _extract_license(info),
        "gated": bool(gated_raw) and gated_raw is not False,
    }
    return snapshot_dir, source, rel_files

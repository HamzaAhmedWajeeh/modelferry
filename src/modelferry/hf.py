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


def _staging_dir_for(staging, repo_id):
    root = staging or os.path.join(os.path.expanduser("~"), ".cache", "modelferry")
    return os.path.join(root, repo_id.replace("/", "__"))


def resolve(repo_id, revision, staging, include, exclude):
    """Resolve a repo to a commit and select files, without downloading anything.

    Returns a dict: commit_sha, source (the §5 source block), files (list of
    (repo_rel_path, size_bytes) for the selected files, sizes from hub metadata),
    total_bytes, local_dir (the staging path the download will land in), endpoint.
    Raises SourceError (exit 3). Separated from download() so pack can validate
    --dest and free space against total_bytes before any bytes move.
    """
    from huggingface_hub import HfApi
    from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError

    token = os.environ.get("HF_TOKEN")
    endpoint = os.environ.get("HF_ENDPOINT", DEFAULT_ENDPOINT)
    local_dir = _staging_dir_for(staging, repo_id)
    api = HfApi(endpoint=endpoint, token=token)

    try:
        info = api.repo_info(repo_id, revision=revision, repo_type="model", files_metadata=True)
    except GatedRepoError as e:
        raise SourceError(
            f"repo {repo_id!r} is gated. Request access and set HF_TOKEN to an account "
            f"that has it, then re-run pack ({e})."
        ) from None
    except RepositoryNotFoundError as e:
        raise SourceError(
            f"repo {repo_id!r} not found. Check the repo id, or set HF_TOKEN if it is "
            f"private ({e})."
        ) from None
    except Exception as e:
        raise SourceError(
            f"could not read repo {repo_id!r} from {endpoint}: {e}. Check your network "
            f"connection and the repo id."
        ) from None

    commit_sha = getattr(info, "sha", None)
    if not commit_sha:
        raise SourceError(
            f"the hub returned no commit sha for {repo_id!r} at revision {revision!r}; "
            f"try a different --revision."
        )

    size_by_path = {}
    for s in getattr(info, "siblings", None) or []:
        name = getattr(s, "rfilename", None)
        if name is not None:
            size_by_path[name] = getattr(s, "size", None) or 0
    wanted = _select(list(size_by_path), include, exclude)
    if not wanted:
        raise SourceError(
            f"no files in {repo_id!r} matched the --include/--exclude patterns; widen them."
        )
    files = [(rel, size_by_path.get(rel, 0)) for rel in wanted]

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
    return {
        "commit_sha": commit_sha,
        "source": source,
        "files": files,
        "total_bytes": sum(size for _, size in files),
        "local_dir": local_dir,
        "endpoint": endpoint,
    }


def download(repo_id, commit_sha, local_dir, endpoint, include, exclude, wanted):
    """Download the selected files at commit_sha into local_dir.

    Returns (snapshot_dir, rel_files). Downloads in local_dir mode (§3): real
    files under --staging, no symlinks, resumable. snapshot_dir also holds a
    .cache/huggingface metadata dir that is never part of rel_files. Raises
    SourceError (exit 3).
    """
    from huggingface_hub import snapshot_download

    token = os.environ.get("HF_TOKEN")
    print(f"downloading {len(wanted)} file(s) from {repo_id} at {commit_sha[:7]} ...")
    try:
        snapshot_dir = snapshot_download(
            repo_id,
            repo_type="model",
            revision=commit_sha,
            local_dir=local_dir,
            allow_patterns=include or None,
            ignore_patterns=exclude or None,
            token=token,
            endpoint=endpoint,
        )
    except Exception as e:
        raise SourceError(
            f"download of {repo_id!r} into {local_dir} failed: {e}. Check your network "
            f"connection and free disk space, then re-run pack to resume."
        ) from None

    rel_files = [
        rel for rel in wanted if os.path.isfile(os.path.join(snapshot_dir, *rel.split("/")))
    ]
    if not rel_files:
        raise SourceError(f"nothing was downloaded for {repo_id!r} into {local_dir}.")
    return snapshot_dir, rel_files

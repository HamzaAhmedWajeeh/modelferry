#!/usr/bin/env bash
#
# Air-gap end-to-end check (SPEC section 11).
#
# Pack the tiny public repo on the host with a small chunk size to force real
# chunking, then run the documented receiving-side sequence (cd into the bundle,
# inspect . then verify .) and unpack inside docker with --network none, first on
# python:3.9-slim and then on rockylinux:9 using the image's own python3 (dnf's
# dependency, no install). The bundled verifier runs with zero network and zero
# third-party packages. Any network attempt or nonzero exit fails the script.
#
# Env overrides: MODELFERRY_PACK (default "uv run --no-sync modelferry").
set -euo pipefail

REPO="hf-internal-testing/tiny-random-gpt2"
CHUNK="1M"
PACK_CMD="${MODELFERRY_PACK:-uv run --no-sync modelferry}"

log() { printf '\n=== %s ===\n' "$*"; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

log "pack $REPO on the host (--chunk-size $CHUNK)"
# shellcheck disable=SC2086
$PACK_CMD pack "$REPO" --dest "$work/bundle" --chunk-size "$CHUNK" --staging "$work/staging"

bundle="$(find "$work/bundle" -mindepth 1 -maxdepth 1 -type d | head -n1)"
if [ -z "$bundle" ]; then
  echo "error: no bundle produced under $work/bundle" >&2
  exit 1
fi
echo "bundle: $bundle"

# The whole point is real chunking: confirm at least one part exists.
if ! find "$bundle/payload" -name '*.mfpart*' -print -quit | grep -q .; then
  echo "error: no chunk parts found; --chunk-size $CHUNK did not force chunking" >&2
  exit 1
fi

# Docker needs a host path it can bind-mount. On Git Bash (Windows) convert to a
# Windows path and stop MSYS from rewriting the in-container paths.
mount_src="$bundle"
if command -v cygpath >/dev/null 2>&1; then
  mount_src="$(cygpath -w "$bundle")"
  export MSYS_NO_PATHCONV=1
fi

run_in() {
  local image="$1" py="$2"
  log "pull $image (host network)"
  docker pull -q "$image" >/dev/null

  # Run the exact sequence MANIFEST.md prints on the receiving side: cd into the
  # bundle, then inspect . and verify . with relative paths. Capture combined
  # output (so a failure still shows its diagnostics), then assert both the exit
  # status and that inspect emitted the recomputed manifest_sha256 line.
  log "inspect + verify inside $image (--network none), the documented sequence"
  local out status
  set +e
  out="$(docker run --rm --network none -v "$mount_src":/bundle:ro "$image" \
    sh -c "cd /bundle && $py tools/modelferry_offline.py inspect . && $py tools/modelferry_offline.py verify ." 2>&1)"
  status=$?
  set -e
  printf '%s\n' "$out"
  if [ "$status" -ne 0 ]; then
    echo "error: the documented inspect + verify sequence failed inside $image" >&2
    exit 1
  fi
  if ! printf '%s\n' "$out" | grep -q '^manifest_sha256: '; then
    echo "error: inspect did not print a manifest_sha256 line inside $image" >&2
    exit 1
  fi

  log "unpack inside $image (--network none)"
  docker run --rm --network none -v "$mount_src":/bundle:ro "$image" \
    sh -c "cd /bundle && $py tools/modelferry_offline.py unpack . /tmp/unpacked"
}

run_in python:3.9-slim python
run_in rockylinux:9 python3

log "air-gap end-to-end OK"

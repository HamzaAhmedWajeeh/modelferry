# modelferry

Pack a Hugging Face model into a chunked, hashed, self-verifying bundle on the
connected side. Verify and reassemble it on the disconnected side with no network
and no third-party dependencies.

The manifest is the product. It doubles as the approval document a security
officer signs off on before the bundle crosses the air gap, and as the checklist
the receiving side runs against on arrival.

![modelferry packing, inspecting, verifying, and unpacking a model](docs/demo.gif)

<!--
TODO(hamza): war-story intro goes here. The time you had to move a 30 GB model
into the isolated environment, the hand-split zip files, the checksum you
computed on a napkin, the security review that had nothing to review. Two or
three short paragraphs, first person. Keep it concrete.
-->

## What it does

modelferry has two sides.

On the connected side, `pack` downloads a model repo pinned to a commit, splits
files that are too big for the transfer media into parts, hashes everything with
sha256, and writes a bundle: the payload, a machine-readable `manifest.json`, a
human-readable `MANIFEST.md` for review, and a copy of the offline verifier
itself.

On the disconnected side, `verify` recomputes every hash and checks it against
the manifest, and `unpack` reassembles the original file tree so vLLM or
transformers can load it directly. Both run from a single standard-library
Python file that ships inside every bundle, so the receiving environment needs
nothing installed and no network. Python 3.9 or newer is the only requirement
there.

## Install

The connected side is a normal Python package:

```
pip install modelferry
```

The disconnected side installs nothing. The verifier travels inside the bundle
at `tools/modelferry_offline.py` and runs against the system Python.

## Five-minute quickstart

This uses a tiny public test repo so you can see the whole round trip in a
couple of minutes without downloading real weights.

That repo ships the same weights three ways: `model.safetensors`,
`pytorch_model.bin`, and `tf_model.h5`. You only want one, so exclude the other
two. The safetensors file that remains is only a few hundred KiB, so pass a
small `--chunk-size` to force it to split into parts and see chunking work:

```
modelferry pack hf-internal-testing/tiny-random-gpt2 --dest ./bundles \
    --chunk-size 200K --exclude "*.bin" --exclude "*.h5"
```

That writes `./bundles/tiny-random-gpt2__<sha>/`. Look at what a reviewer would
see:

```
modelferry inspect ./bundles/tiny-random-gpt2__<sha>
```

Now pretend you have carried the bundle across the air gap. Verify it with the
bundled tool, exactly as the receiving side would, with no modelferry install:

```
python3 ./bundles/tiny-random-gpt2__<sha>/tools/modelferry_offline.py \
    verify ./bundles/tiny-random-gpt2__<sha>
```

Then reassemble the model tree:

```
python3 ./bundles/tiny-random-gpt2__<sha>/tools/modelferry_offline.py \
    unpack ./bundles/tiny-random-gpt2__<sha> ./model
```

`./model` now holds the original repo tree, and `./model/UNPACK_RECEIPT.json`
records that it was verified on the way in.

## Packing a real model

```
modelferry pack Qwen/Qwen2.5-7B-Instruct --dest /media/usb --revision main
```

Useful options:

- `--revision` pins the download. It defaults to `main` and is resolved to a
  commit SHA before anything is fetched. The manifest records both the requested
  revision and the resolved SHA.
- `--chunk-size` sets the maximum part size. It defaults to `3900M`, which fits
  under FAT32's 4 GiB file limit with margin. Pass `16G` for larger media, or
  `none` to store every file whole.
- `--include` and `--exclude` are fnmatch patterns against repo-relative paths.
  Exclude wins. The common case is `--exclude "*.bin"` when safetensors already
  cover the weights.
- `--staging` is where the download lands before packing. It defaults to
  `~/.cache/modelferry/`. Re-running `pack` after an interruption resumes the
  download.

Hugging Face auth is the `HF_TOKEN` environment variable, and only that. It is
never a flag and never written into a bundle, manifest, receipt, or log. Custom
hub endpoints work through the standard `HF_ENDPOINT` variable, and the endpoint
used is recorded in the manifest.

## The manifest

`manifest.json` is deterministic: the same inputs produce a byte-identical file,
so it diffs cleanly and can be compared across sites. It records the source repo
and resolved commit, the license and gated flag, every file with its size and
whole-file sha256, the parts each chunked file was split into with their own
hashes, and the sha256 of the bundled verifier.

`MANIFEST.md` is the same information rendered for a human reviewer. It names two
moments: before transfer, approve and keep a copy of it; on arrival, compare the
`manifest.json` checksum to the copy you kept, then run verify. If the license
could not be determined from repo metadata it is recorded as `UNKNOWN` and
flagged at the top of the document.

## Trust model

Stated honestly, because this is the part a security review turns on.

v1 protects against accidental corruption, incomplete transfers, media errors,
and casual tampering with payload files. Any of those shows up as a hash
mismatch, a missing part, or an extra file, and verify exits non-zero and names
what failed.

v1 does not protect against an adversary who can modify the payload, the
manifest, and the bundled verifier together. Defending against that requires
signature verification with a key held out-of-band, which is planned for v1.1
(minisign).

The mitigation available today is out-of-band verification of the verifier.
`manifest.json` records the sha256 of the bundled `offline.py`, and every
release publishes the canonical `offline.py` hash in its release notes. A
receiving site can check the bundled verifier against that published hash before
trusting it, or ignore the bundled copy and bring its own known-good verifier.

## Exit codes

```
0  success
1  integrity failure (verify mismatch, missing or extra file, unpack hash
   failure, path-safety violation)
2  usage error (bad arguments, unknown manifest version, malformed manifest)
3  source error (network failure, HF auth, 404, gated without token)
4  local filesystem error (permissions, disk full, destination exists
   without --force)
```

## Development

```
uv sync
uv run pytest -m "not network"     # default, offline
uv run pytest                      # includes the network integration test
uv run ruff check . && uv run ruff format .
bash scripts/e2e_airgap.sh         # docker, runs verify/unpack with --network none
```

`SPEC.md` is the contract. `src/modelferry/offline.py` is the trust surface: it
is standard library only, runs on CPython 3.9, imports nothing from the
modelferry package, and is copied verbatim into every bundle. A test enforces
all of that.

## License

Apache-2.0. See [LICENSE](LICENSE).

# modelferry

Pack a Hugging Face model into a chunked, hashed, self-verifying bundle on the
connected side. Verify and reassemble it on the disconnected side with no network
and no third-party dependencies.

I thought air-gapped just meant no internet. Same stack, cut the outbound
route. How hard could it be.

Then I had to get a model in. You don't pull 40GB from Hugging Face and go.
It moves through whatever channel the client approves, checksums get verified
by hand, and the file usually gets chopped up because the transfer media won't
take a single object that size. I've watched senior engineers lose an entire
afternoon to this. At the end of the afternoon, the only thing you can hand
the security officer is "trust me, it's the same file."

modelferry does that in one command and gives you the paperwork at the end.

The manifest is the product. It doubles as the approval document a security
officer reviews and approves before the bundle crosses the air gap, and as the
checklist the receiving side runs against on arrival.

![modelferry packing, inspecting, verifying, and unpacking a model](https://raw.githubusercontent.com/HamzaAhmedWajeeh/modelferry/main/docs/demo.gif)

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

`pack --sign` optionally signs the manifest on the connected side, and
`verify-signature` checks that signature against a trusted key. That authenticity
check runs on the connected or approval side where the key lives, not on the bare
host. Signing is covered below.

## Install

The connected side is a command-line tool, so install it in its own isolated
environment. Nothing imports modelferry, and an isolated install keeps its
dependency versions from colliding with whatever your projects have pinned.

```
uv tool install modelferry
```

or

```
pipx install modelferry
```

Plain `pip install modelferry` works too if you would rather put it in an
existing environment.

The disconnected side installs nothing. The verifier travels inside the bundle
at `tools/modelferry_offline.py` and runs against the system Python.

## Five-minute quickstart

This uses a tiny public test repo so you can see the whole round trip in a
couple of minutes without downloading real weights.

That repo ships one model in three serialization formats: `model.safetensors`,
`pytorch_model.bin` (PyTorch), and `tf_model.h5` (TensorFlow). You only need one,
so exclude the other two. The safetensors file that remains is only a few hundred
KiB, so pass a small `--chunk-size` to force it to split into parts and see
chunking work:

```
modelferry pack hf-internal-testing/tiny-random-gpt2 --dest ./bundles \
    --chunk-size 200K --exclude "*.bin" --exclude "*.h5"
```

That writes `./bundles/tiny-random-gpt2__<sha>/`. `pack` prints the exact bundle
path on its last line, so use whatever it printed in place of `<sha>` below.

Now pretend you have carried the bundle across the air gap. Everything from here
runs from inside the bundle with the bundled tool, exactly as the receiving side
would, with no modelferry install. This is the same three-command sequence
`MANIFEST.md` prints and `scripts/e2e_airgap.sh` proves under `--network none`.

```
cd ./bundles/tiny-random-gpt2__<sha>
```

Look at what a reviewer would see, then check every hash against the manifest:

```
python3 tools/modelferry_offline.py inspect .
python3 tools/modelferry_offline.py verify .
```

`inspect` prints the recomputed `manifest_sha256` to compare against the copy
you approved before transfer, and `verify` prints "verify OK" only when every
object matches. Then reassemble the model tree:

```
python3 tools/modelferry_offline.py unpack . ../model
```

`../model` now holds the original repo tree, and `../model/UNPACK_RECEIPT.json`
records that it was verified on the way in.

## Signing a bundle

Signing is optional and additive. It adds an authenticity check on top of the
integrity check above: a signed bundle proves its manifest was signed by a key you
trust, so a bundle someone else rebuilt from scratch fails verification even when
its own hashes are internally consistent. Packing without `--sign` is unchanged.

You need an ed25519 keypair. Generate one outside any repo and keep the secret key
off version control:

```
python3 -c "from modelferry.signing import Ed25519Signer; Ed25519Signer.generate_keypair('signing.key', 'signing.pub')"
```

Point `MODELFERRY_SIGNING_KEY` at the secret key and pack with `--sign`:

```
export MODELFERRY_SIGNING_KEY="$PWD/signing.key"
modelferry pack hf-internal-testing/tiny-random-gpt2 --dest ./bundles \
    --chunk-size 200K --exclude "*.bin" --exclude "*.h5" --sign
```

The bundle now carries a detached signature at `manifest.json.sig` over the exact
`manifest.json` bytes, plus a `signing` block in the manifest. Pack self-verifies
that signature before it finishes, so a bad signature fails the pack rather than
shipping. Check it yourself against the public key:

```
modelferry verify-signature ./bundles/tiny-random-gpt2__<sha> --public-key signing.pub
```

It prints `VALID` when the signature matches the trusted key. Any other result
exits non-zero: `BAD_SIGNATURE` if the manifest was altered or signed by another
key, `KEY_MISMATCH` if the signature is valid but for a different key than the one
given, `MISSING_SIG` if the sidecar is absent, or `UNSIGNED` for a bundle that was
never signed. None of those should be trusted.

The public key is what a verifier trusts, and it has to reach them out-of-band.
modelferry has no signing key of its own: you sign your bundles with your key, and
whoever operates the approval authority for the receiving environment distributes
the trusted public key, the same people who approve the manifest checksum. The
`verify-signature` check runs where that key lives, on the connected or approval
side, not on the bare disconnected host. See the [Trust model](#trust-model) for
the full picture, including why the bare-host integrity check does not verify the
signature.

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
- `--sign` signs the manifest so the bundle can be checked for authenticity with
  `verify-signature`. The signing key path comes from the `MODELFERRY_SIGNING_KEY`
  environment variable, only that, never a flag and never written into the bundle.
  `--sign` with no key set is an error, not a silent unsigned pack. Without
  `--sign` the bundle is unsigned. See [Signing a bundle](#signing-a-bundle).

Hugging Face auth is the `HF_TOKEN` environment variable, and only that. It is
never a flag and never written into a bundle, manifest, receipt, or log. Custom
hub endpoints work through the standard `HF_ENDPOINT` variable, and the endpoint
used is recorded in the manifest.

## The manifest

`manifest.json` is deterministic: the same inputs produce a byte-identical file,
so it diffs cleanly and can be compared across sites. It records the source repo
and resolved commit, the license and gated flag, every file with its size and
whole-file sha256, the parts each chunked file was split into with their own
hashes, and the sha256 of the bundled verifier. When a bundle is signed it also
carries an optional `signing` block, and the detached signature sits beside it at
`manifest.json.sig`.

`MANIFEST.md` is the same information rendered for a human reviewer. It names two
moments: before transfer, approve and keep a copy of it; on arrival, compare the
`manifest.json` checksum to the copy you kept, then run verify. If the license
could not be determined from repo metadata it is recorded as `UNKNOWN` and
flagged at the top of the document.

## Trust model

Stated honestly, because this is the part a security review turns on. modelferry
checks two different things with two different tools.

Integrity is whether the bytes that arrived match the manifest. It protects
against accidental corruption, incomplete transfers, media errors, and casual
tampering with payload files. Any of those shows up as a hash mismatch, a missing
part, or an extra file, and verify exits non-zero and names what failed. Integrity
is checked by the bundled `offline.py`, standard library only, on the bare
disconnected host with no network and no key.

Authenticity is whether the manifest was signed by a key you trust. When a bundle
is signed (`pack --sign`) and the verifier has the trusted public key, authenticity
is checkable: `modelferry verify-signature` checks the detached signature over
`manifest.json` against that key. A bundle an attacker rebuilt from scratch, with
its own internally consistent payload, manifest, and hashes, would pass its own
integrity check but fail signature verification against the real key, because the
attacker cannot produce that key's signature.

The boundary, stated precisely so it isn't oversold: the bare-host integrity
verifier does not check the signature. Signature verification is a separate
connected-side tool that holds the trusted key, and it is not copied into the
bundle. So on a truly bare air-gap host with only system Python and no trusted key
on hand, you get integrity, and authenticity is established upstream, at the
approval or admission step where the key lives. Signing protects that approval
chain. The trusted public key has to reach verifiers out-of-band, and it is the
approval authority for the receiving environment that distributes it, the same
people who already approve the manifest checksum. modelferry has no signing key of
its own and does not distribute keys: `pack --sign` signs your bundles with your
key, for your environment. The `key_id` in the manifest lets a verifier confirm
which key a bundle claims to be signed by, but trust in that key comes from the
operator's out-of-band distribution, not from modelferry. This is key-based
signing verified against a known key, not end-to-end authenticity on a bare host
that has no key.

Signing is additive. An unsigned bundle still packs and still verifies for
integrity; it just carries no authenticity claim, which verify-signature reports
as UNSIGNED rather than as valid.

The verifier itself is also checkable out-of-band. `manifest.json` records the
sha256 of the bundled `offline.py`, and every release publishes the canonical
`offline.py` hash in its release notes. A receiving site can check the bundled
verifier against that published hash before trusting it, or ignore the bundled
copy and bring its own known-good verifier.

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

# Resolve every dependency to its declared floor and run the suite, so the
# lower bounds in pyproject are tested claims. The network test is included so
# the huggingface_hub floor is exercised for real.
uv sync --resolution lowest-direct && uv run pytest -m "not network" && uv run pytest -m network

# Build the wheel and round-trip pack/verify/unpack from a clean venv with the
# repo off the path, so the installed package (not the checkout) is exercised.
uv build && uv venv /tmp/wenv && uv pip install --python /tmp/wenv/bin/python dist/*.whl
```

`SPEC.md` is the contract. `src/modelferry/offline.py` is the trust surface: it
is standard library only, runs on CPython 3.9, imports nothing from the
modelferry package, and is copied verbatim into every bundle. A test enforces
all of that.

## License

Apache-2.0. See [LICENSE](LICENSE).

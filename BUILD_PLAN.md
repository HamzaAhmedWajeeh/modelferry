# modelferry — Build Plan

**The signed, compliant supply chain for open AI models running inside air-gapped and regulated networks.**

This is the execution plan, handed to Claude Code one task at a time. Every phase lists numbered tasks, the files each touches, the key interfaces, named acceptance tests, and a definition of done. The acceptance tests are how "works offline" and "detects tampering" become things CI proves rather than things a README claims.

## The one strategic decision that shapes everything

We build the full platform, but we **ship each phase as a real release** instead of hiding five months of work on a long-lived branch. Same architecture, same destination. The difference is that every phase becomes a public milestone that ships, gets a launch post, and gathers feedback, rather than a held breath that decays.

Why: a solo, side-of-desk build does not die from bad code. It dies in month three when nothing has shipped, no user has reacted, and the reason to keep going has quietly evaporated. Phased releases refuel that reason from outside your own head, and each release is also free market validation. If a phase lands and people care, that is the signal to build the next. If it lands flat, you learned it before sinking two more months in.

Release map:

- **0.2.0 — Signing** (Phase 0 here). Closes the authenticity gap the launch post named. 2–3 weeks. Ships.
- **0.3.0 — Closure engine** (Phase 1). Bundles that actually run offline: wheels, image, model, one command. A large user-facing win and a second launch.
- **0.4.0 — Registry preview** (Phase 2). The appliance that admits, versions, serves, audits. The thing that turns a design-partner conversation into a pilot.
- **0.5.0 / 1.0 — Trust, compliance, delta, hardening** (Phases 3–5). SBOM, policy, CVE scanning, delta transport, clean-room install.

A parallel, non-code track runs alongside Phase 0–1 and is as important as any task in it: **turn one real disconnected environment into a named design partner.** See Section 12.

## How to drive Claude Code with this doc

- Keep `BUILD_PLAN.md`, `RESOURCES.md`, `SPEC.md`, and `CLAUDE.md` in the repo root so the plan is always in context.
- Give Claude Code one task at a time, addressed as "Phase N, Task M." Paste the task, its files, and its acceptance test, and ask for the test first, then the implementation.
- Definition of done for every task: the named test passes, it runs under the air-gap harness where the task is an inside-gap step, and the checkbox here is ticked.
- House rule, the most important constraint in this document: **the embedded offline verifier (`src/modelferry/offline.py`) stays standard-library only, forever, and verifies integrity only.** It never gains a crypto dependency, never verifies a signature itself, never grows toward hand-rolled cryptography. If a task would add a dependency to it, the task is wrong. A CI test enforces this mechanically (`test_offline_stdlib_lint`), proven to fail on a real non-stdlib import.

## Repository and git workflow

Work in the existing modelferry repo. The core primitives are extracted from code already validated on a real 65GB model, and the repo's history from packer to platform is part of the story a design partner and a YC reviewer read.

- `main` is the launched CLI, currently at v0.1.0. It advances only at each release, by merging `develop`.
- `develop` is the long-lived integration trunk, cut from `main` once. Every phase lands here.
- `phase-*` branches are cut from `develop` and merged back when that phase's acceptance tests pass.
- At each release milestone, `develop` merges to `main` and a version is tagged. Unlike the original "one merge at the end" plan, we merge and tag at every phase boundary, because shipping is the point.

CI (secret scanner, unit tests, the stdlib guard, the harness) runs on all branches. Branch protection required on `main` and `develop`.

The repo is public, so every branch is public the moment it is pushed. Anything that must stay private goes in a separate private repo, never a branch.

## Secret hygiene (non-negotiable, because the repo is public)

- Never commit a secret. Bots scrape public pushes within minutes, and git history keeps a secret even after a later commit deletes the file. Any secret that reaches a public branch is burned and must be rotated.
- All config reads from environment variables. Commit a `.env.example` with placeholders only. `.gitignore` covers `.env`, `*.key`, `sec.key`, `*.pem`, and credential files.
- A pre-commit secret scanner (gitleaks or trufflehog) blocks a commit containing a secret, and the same scan runs in CI.
- **The minisign signing key never touches the repo.** It is generated and stored outside version control. A leaked signing key lets anyone forge a bundle the appliance trusts, which breaks the entire trust model.

---

## Phase 0 — Signing (ships as 0.2.0)

**Goal:** a bundle's manifest is cryptographically signed on the connected side, and the signature is verifiable wherever a trusted key and a crypto library exist (pack-time self-check, and later the appliance), **without adding any dependency or crypto to the frozen offline.py integrity verifier.**

**The load-bearing design decision, settled after reading the real offline.py:**

Integrity and authenticity are separate concerns handled by separate tools.

- `offline.py` answers "did these bytes arrive intact." That is **integrity**. It stays stdlib-only, runs on the bare air-gap host, and is unchanged except to accept schema 2 for integrity checking. No crypto ever enters it.
- A new, separate tool answers "was this manifest signed by a key I trust." That is **authenticity**. It is allowed dependencies (PyNaCl, with the `minisign` binary as documented interop), and it runs where a key and a crypto library live: pack-time, and the appliance at admission.

Why this is correct and not a dodge: the two questions have different trust models and different environments. The bare air-gap host verifies integrity against a manifest whose hash was approved out-of-band (exactly what MANIFEST.md already instructs). The signature protects that **approval chain** upstream; it is not part of the unpack step on the disconnected host. This is why offline.py barely moves.

Branch: `phase-signing` off `develop`.

- [x] **0.1** Branch setup and make the stdlib guard load-bearing. Cut `develop` from `main`, cut `phase-signing` from `develop`. Confirm `test_offline_stdlib_lint` asserts offline.py imports only stdlib, confirm CI runs on all branches, and prove the guard fails on a deliberate `import nacl` before reverting. *(Done: commit c122e20 broadened CI push trigger to all branches; guard proven to fail on real non-stdlib import.)*

- [x] **0.2** The `Signer` interface. New `src/modelferry/signing.py`: `class Signer(Protocol)` with `sign(manifest_bytes) -> bytes` and `key_id() -> str`, plus a `MinisignSigner` (or PyNaCl-backed) implementation. Connected side, so dependencies allowed. Design so a KMS/HSM signer drops in later. Key path read from env var, never a flag, never committed. *(Done: commit 2cec6ac. `Ed25519Signer` (PyNaCl) is the concrete impl; pynacl added as a runtime dep, SPEC §12 and CLAUDE.md updated to match; stdlib guard on offline.py still green on Linux CI.)*

  **Decision — native key format (settled at 0.2):** the ed25519 signer uses a **raw hex-encoded 32-byte seed** as its on-disk key format (`generate_keypair` writes seed hex + public hex). This is deliberately **not** minisign's format. Rationale: 0.2.0 controls both ends (PyNaCl produces the signature and the pack-time self-check / `verify_signature.py` consume it); nothing this release hands a key or signature to the `minisign` binary, so matching minisign now buys zero working interop and pulls in its full envelope (base64 key blobs with algorithm tag + key id, scrypt-encrypted secret keys, the `.minisig` signature format with a signed trusted-comment). True minisign interop is deferred to a later `MinisignSigner` behind the same Protocol (see §8, "minisign as the documented interop path"). **Consequence for 0.3:** the manifest `algorithm` string must name what is actually produced (`"ed25519"`, not `"minisign-ed25519"`) until a real minisign signer lands; `minisign-ed25519` is reserved for that.

- [ ] **0.3** Manifest schema 2. Bump `MANIFEST_VERSION` to 2 in `manifest.py`. Add a `signing` block: `{"algorithm": "minisign-ed25519", "key_id": "<id>", "signature_file": "manifest.json.minisig"}`. Payload structure unchanged. The manifest never contains its own signature (circular); the signature is always the external `.minisig` sidecar, computed over the final serialized manifest bytes. Serialization stays deterministic.

- [ ] **0.4** offline.py accepts schema 2 for integrity — the one careful edit to the frozen file. Change `SUPPORTED_MANIFEST_VERSION` to accept `{1, 2}`. That is the only functional change: offline.py verifies integrity for both schemas, imports no crypto, does not verify signatures, stays under the line cap. Update the frozen-file rationale and SPEC to state the split explicitly. The stdlib guard from 0.1 must stay green.

- [ ] **0.5** The signature verifier — a separate tool, not offline.py. New `src/modelferry/verify_signature.py`: takes a bundle and a trusted public key, checks `manifest.json.minisig` against `manifest.json`. Dependencies allowed. This is what the appliance calls at admission and what a security officer runs connected-side. It is explicitly not copied into the bundle as a bare-host requirement.

- [ ] **0.6** Pack-time signing and self-check. In `pack.py`/`cli.py`: after writing the manifest, if a signing key is configured, sign it, write `manifest.json.minisig`, then run 0.5's verify as a pack-time self-check (mirrors the existing post-pack integrity self-verify). Gated by `--sign` or `MODELFERRY_SIGNING_KEY`. Unsigned packing still works; signing is additive.

- [ ] **0.7** MANIFEST.md and README reflect signing. MANIFEST.md gains a signing section when signed. The README's honest-limit paragraph flips: authenticity is covered when the bundle is signed and the verifier has the trusted key, with the boundary stated precisely (the bare-host integrity check still does not verify the signature; that is done at approval/admission where the key lives).

- [ ] **0.8** Release 0.2.0. Tag, publish to PyPI, GitHub release. The release notes publish the canonical `offline.py` **verifier hash** (as every release has since 0.1.0). They do NOT publish any signing key fingerprint: modelferry has no signing key of its own. `pack --sign` is a capability for users to sign THEIR bundles with THEIR keys, distributed out-of-band by whoever operates the approval authority for the receiving environment. There is no canonical modelferry signing key, so no release publishes one.

**Acceptance tests (Phase 0):**
- `test_offline_stdlib_lint` — offline.py imports only stdlib; fails on a real non-stdlib import. *(passing)*
- `test_signer_roundtrip` — sign a fixed byte string, verify against the public key in-process.
- `test_signing_key_never_in_repo` — no key material or key path literal committed.
- `test_manifest_v2_deterministic` — same inputs, byte-identical v2 manifest.
- `test_v2_bundle_verifies_integrity` — a v2 bundle passes `verify` on the unchanged integrity verifier.
- `test_v1_bundle_still_verifies` — no regression for existing bundles.
- `test_good_signature_verifies` / `test_tampered_manifest_fails_signature` / `test_wrong_key_fails` / `test_missing_signature_fails`.
- `test_pack_signs_when_key_present` / `test_pack_unsigned_without_key` / `test_packed_signature_self_verifies`.

**Done when:** a real model packs to a signed bundle, the signature verifies where the key lives, integrity still verifies on the bare stdlib host for both schemas, the guard holds, and 0.2.0 is published with the canonical offline.py verifier hash in the release notes (no signing key is published; there is no canonical modelferry signing key).

---

## Phase 1 — Closure engine (ships as 0.3.0)

**Goal:** a bundle carries everything to run the model offline, and a real model goes from Hugging Face to serving inside `--network none` using only the bundle.

- [ ] **1.1** `core/closure/resolve.py`: given a target (repo, revision, extras), produce a pinned `lock.txt` with `uv pip compile`. Record exact index URLs.
- [ ] **1.2** `core/closure/wheelhouse.py`: build the wheelhouse for the target platform. See Section 7 for flags and sdist handling.
- [ ] **1.3** `core/closure/image.py`: `docker save` the inference-server image into `payload/images/`. Record image digest in `compat.json`.
- [ ] **1.4** `core/closure/model.py`: fetch model artifacts from HF pinned to `revision`, honoring an HF token for gated repos. **The hash of record is computed post-fetch** — the manifest describes the bytes actually fetched, never claims byte-identity to HF (LFS re-uploads and mirror differences happen).
- [ ] **1.5** `core/compat.py`: emit `compat.json` (arch, minimum NVIDIA driver / compute capability, python version, footprint, image digest, torch build).
- [ ] **1.6** CLI: `modelferry pack <repo> --revision <sha> --server vllm --out bundle/`.
- [ ] **1.7** Offline reconstruction: verify, `pip install --no-index --find-links payload/wheelhouse -r payload/lock.txt`, `docker load`, load model, serve one completion.
- [ ] **1.8** Preflight: read `compat.json` and fail clearly if host driver or compute capability is below requirement, before any transfer or load.
- [ ] **1.9** Driver compatibility matrix (added, load-bearing). Ship a documented "these bundles run on driver X and up" table, and pack at least one bundle targeting an older CUDA (12.1, driver ~530) as a fallback. The first pilot's disconnected host often runs a frozen old driver IT will not touch; without a fallback, day one is a driver argument, not a demo.

**Acceptance tests:**
- `test_wheelhouse_offline_install` (GPU runner) — in `--network none`, install from wheelhouse succeeds and imports torch with CUDA.
- `test_model_serves_offline` (GPU runner) — vLLM loads the bundled model and returns a completion offline.
- `test_sdist_only_dependency_flagged` — a target pulling an sdist-only non-prebuilt dependency is caught at pack time, not load time.
- `test_compat_preflight_fails_on_mismatch` — a fabricated low-driver compat.json fails preflight.
- `test_older_cuda_bundle_serves` — the 12.1 fallback bundle serves on an older-driver host.

**Done when:** Qwen2.5-14B goes HF → served inside a network-isolated container using only the bundle, the driver fallback works, and the tests pass. Ships as 0.3.0.

**Honesty call to settle here:** is the wheelhouse a real independent install path, or a fallback with the image as primary runtime? flash-attn plus the torch/CUDA/Python matrix is the most common reason offline vLLM installs fail, and "prefer the image path" quietly means the image, not the wheelhouse, carries such models. Pick the honest story and make `test_wheelhouse_offline_install` match it, so it does not fail on exactly the models people want.

---

## Phase 2 — Registry as the product (ships as 0.4.0 preview)

**Goal:** an appliance that admits, versions, serves three ways, and audits.

- [ ] **2.1** Ingest: chunked upload to `POST /bundles/`, store chunks in the CAS with refcounting, create a `pending` version. Reject on manifest hash mismatch.
- [ ] **2.2** Admission verification: check the minisign signature against the appliance's trusted key (this is where 0.2.0's `verify_signature` runs server-side) and validate the manifest before accepting. Failures never create an approved version.
- [ ] **2.3** RBAC and approval state machine: `pending → approved | rejected`, `approved → revoked`. Enforce separation of duties (creator cannot approve).
- [ ] **2.4** Serve models: materialize an approved version to a verified directory an inference host mounts.
- [ ] **2.5** Serve offline PyPI: static PEP 503 index from the CAS wheels.
- [ ] **2.6** Serve OCI: embed zot, load the bundled image, proxy `/v2/`.
- [ ] **2.7** Audit log — a **hash chain** (not a Merkle tree): `entry_hash = sha256(prev_hash || canonical(actor, action, target, detail, created_at))`, fixed genesis. Detects tampering of past rows. Known limitation stated in one sentence: a full-history rewrite by someone who controls the appliance is only caught if the genesis hash or a periodic anchor is recorded off-box. It is tamper-evident, not tamper-proof.
- [ ] **2.8** Revocation boundary (added, load-bearing). Revocation blocks future admission and serving from the appliance; it does not reach already-deployed inference hosts, which are re-verified on next materialize. Use the `deployments` table as the enforcement surface: a reconciliation check flags running deployments of a now-revoked version. Document this boundary explicitly — a CISO will find it otherwise.
- [ ] **2.9** Web UI (templates + HTMX): catalog, version detail, approval queue, audit view.

**Acceptance tests:**
- `test_ingest_rejects_bad_manifest` / `test_admission_rejects_bad_signature` / `test_separation_of_duties`.
- `test_pip_install_from_registry_offline` / `test_docker_pull_from_registry_offline`.
- `test_audit_chain_detects_tamper`.
- `test_revoked_deployment_flagged` — a running deployment of a revoked version is surfaced by reconciliation.

**Done when:** two versions ingested, approval enforced, an isolated host pulls wheels and image from the registry and serves, revocation reconciliation works, and the tests pass. Ships as 0.4.0 preview — the release you take to a design partner.

---

## Phase 3 — Trust and compliance (folds into 0.5.0)

**Goal:** the appliance speaks auditor and CISO language and refuses non-compliant models.

- [ ] **3.1** Signing hardening: KMS/HSM-backed signer via the 0.2.0 `Signer` interface. cosign as the enterprise implementation.
- [ ] **3.2** in-toto: signed provenance statement (builder, modelferry source commit, model source and digest, scanner and policy results).
- [ ] **3.3** SBOM: `core/sbom.py` runs syft over the wheelhouse and image, injects model metadata, produces CycloneDX and SPDX. Verify current spec support for the ML fields when implementing — they are evolving.
- [ ] **3.4** License detection: read model-card metadata and LICENSE files, map to SPDX ids, classify permissive / copyleft / non-commercial / restrictive. Expect this to be heuristic and fiddly; model-card license metadata is wildly inconsistent.
- [ ] **3.5** CVE scanning: pip-audit or osv-scanner over `lock.txt`, trivy or grype over the image, aggregated into a signed `vuln-report.json`. Scanners run connected-side against a snapshotted vulnerability DB; only the signed report crosses the gap.
- [ ] **3.6** Policy engine: `core/policy.py` evaluates `policy.yaml` at two gates (pack and admission).

**Acceptance tests:** `test_policy_refuses_bad_license`, `test_policy_refuses_critical_cve`, `test_admission_reruns_policy`, `test_sbom_contains_model_and_deps`, `test_compliance_export_signed`.

**Sizing note:** this phase slips more than any other in a side-of-desk build. syft's CycloneDX ML extension is in flux and license classification is hand-tuned heuristics. Budget double the naive estimate.

---

## Phase 4 — Delta and transport (folds into 0.5.0)

**Goal:** updates ship megabytes not gigabytes, and higher-security transports work. Respect the one-way constraint: only a small, **exact** (never probabilistic — a Bloom filter over a diode silently drops chunks) inventory crosses outward; the larger delta comes inward.

- [ ] **4.1** Exact inventory export: `GET /inventory/chunks`, sorted held-chunk hashes, varint-delta-encoded, zstd-compressed.
- [ ] **4.2** Delta pack: diff a new version's chunk set against an imported inventory, write a bundle of only missing chunks plus the full manifest.
- [ ] **4.3** Reconstruction: rebuild from held chunks plus delta, verify byte-identical against the manifest before `pending`.
- [ ] **4.4** Diode reconciliation manifest: completeness proof with no back-channel.
- [ ] **4.5** OCI export path for registry-to-registry ingest without USB.
- [ ] **4.6** Optional AES-256 media encryption (age or libsodium), documented key handling.
- [ ] **4.7** Resumable unpack via a receipt tracking applied chunks.

**Acceptance tests:** `test_delta_ships_only_missing_chunks`, `test_delta_reconstructs_byte_identical`, `test_delta_inventory_is_exact`, `test_resumable_unpack`. Record the size ratio — it is a YC application number.

---

## Phase 5 — Appliance packaging and pilot hardening (ships as 1.0)

**Goal:** something a real security review passes, installed from media into a clean room.

- [ ] **5.1** Package the appliance itself as an offline-installable, signed modelferry bundle (dogfood).
- [ ] **5.2** Install, upgrade, backup, restore procedures with scripts.
- [ ] **5.3** Observability on the Celery workers (dogfood celerypeek).
- [ ] **5.4** Hardening: secrets, TLS, least-privilege, air-gap install verification.
- [ ] **5.5** Promote to 1.0.

**Acceptance tests:** `test_clean_room_offline_install`, `test_full_loop_e2e`.

---

## Section 7 — Closure engine, in detail

The part that makes a bundle runnable and the part clones skip.

Wheelhouse build (connected side):
```bash
uv pip compile requirements.in -o lock.txt
pip download -r lock.txt -d payload/wheelhouse \
  --only-binary=:all: --platform manylinux_2_28_x86_64 \
  --python-version 311 --implementation cp
pip download torch --index-url https://download.pytorch.org/whl/cu124 \
  --only-binary=:all: --platform manylinux_2_28_x86_64 \
  --python-version 311 --implementation cp -d payload/wheelhouse
```

Inside the gap:
```bash
pip install --no-index --find-links payload/wheelhouse -r payload/lock.txt
docker load -i payload/images/vllm.tar
vllm serve /path/to/materialized/model --host 0.0.0.0 --port 8000
```

Gotchas, each with a test: sdist-only / build-required deps (flash-attn is the classic; prefer the image path and detect the gap at pack time), sharded safetensors (hash every shard and the index), custom-code models (`trust_remote_code`; fetch the `.py`, flag in policy), gated repos (HF token, restrictive license into classification), quantized/alternate formats (AWQ/GPTQ and GGUF, since buyers do not all run vLLM), FAT32 (chunk parts ≤ 4GiB — the 4MiB CAS chunk and the 4GiB FAT32 part-file cap are two different numbers; do not conflate them).

Documented limitation: modelferry bundles the CUDA userspace (torch wheels, server image), not the kernel driver. The gap host must have a compatible driver. `compat.json` plus preflight plus the 1.9 fallback bundle turn this into an early, clear failure and a documented workaround, not a runtime crash.

## Section 8 — Trust, signing, SBOM, policy

Keyless Sigstore needs a live CA and transparency log, so it cannot be the inside-gap verification path. Key-based signing, verified offline against a bundled trust root. minisign for v1, `Signer` interface designed for KMS/HSM cosign later. If a customer wants Sigstore bundles, produce them connected-side and verify offline with a bundled trust root; never depend on Rekor reachability inside the gap.

Policy schema (`policy.yaml`):
```yaml
version: 1
require_signature: true
allowed_licenses: [Apache-2.0, MIT, BSD-3-Clause, "class:permissive"]
denied_licenses: ["class:non-commercial"]
flag_remote_code: true
max_cve_severity: high
allowed_sources: [huggingface.co]
```
Evaluator returns `{passed, violations}`, runs at pack time (fail fast) and admission (enforcement of record).

## Section 10 — The air-gap test harness

Every inside-gap step runs inside `docker run --network none` with the bundle mounted read-only. Any accidental network call fails the test. Add an explicit egress probe the tests call to assert a socket is refused. Two modes: a CPU Python container for wheel reconstruction and a tiny model load, and the actual server image for the offline serve proof (GPU runner).

## Section 11 — End-to-end acceptance matrix

Positive: (1) pack Qwen2.5-14B, FAT32 transfer, admit, serve in `--network none`. (2) isolated client installs wheels via the registry index and pulls the image. (3) small-change delta transfers a fraction and reconstructs byte-identically. (4) compliance report verifies against the appliance key.

Negative, each failing in the right way: (5) flipped payload byte → integrity exit code. (6) tampered `manifest.json` → signature verification fails, admission refuses. (7) unsigned or wrong-key bundle → never approved. (8) missing wheel → flagged at pack time. (9) non-commercial model under permissive-only policy → refused at pack gate. (10) fabricated low-driver compat.json → preflight fails. (11) corrupt delta chunk → byte-identical check fails. (12) edited audit row → chain verify false. (13) any inside-gap network call → harness fails it.

## Section 12 — The design-partner track (as important as the code)

The plan's value as a YC application depends entirely on turning one real disconnected environment into a named design partner who will say "yes, we need this." The most valuable asset here is access to a real air-gapped government environment with a real model need. Have that conversation during Phase 0–1, not deferred to Phase 5. See the conversation script kept alongside this plan. Spend as much energy on that one conversation as on any single phase.

## Section 13 — What to cut for v1

No Kubernetes operator (docker-compose is enough). No multi-node HA or web-scale multi-tenancy. No React UI until a design partner needs it (templates, HTMX, Django admin). No OPA/Rego (the YAML evaluator suffices). No keyless Sigstore.
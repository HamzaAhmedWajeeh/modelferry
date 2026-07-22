# modelferry — Resources and Test Environment

What you need to build modelferry and prove it works end to end. Almost all of it is free and open source. The only real cost is intermittent GPU rental, and that does not start until Phase 1 — Phase 0 (signing, 0.2.0) needs nothing but your laptop.

## The one thing to internalize first

Only two moments ever need a GPU: the Phase 1 serve-proof and the final end-to-end run. Everything else — signing, the CLI, the closure resolver, the registry, chunking, the CAS, SBOM, scanning, policy, delta, the audit chain, the offline PyPI and OCI serving — is CPU-only and runs on your laptop. Do not buy a GPU box. Rent one by the hour for the handful of runs that serve a model, and build the other 90 percent locally for free.

**Phase 0 (0.2.0 signing) specifically needs zero GPU and zero rental.** It is pure connected-side crypto and manifest work on the machine you already have.

## Phase 0 (signing) resources — start here

Everything for 0.2.0 is on your existing setup:

- Your dev laptop, the modelferry repo, Python 3.11.
- **minisign** (the binary) and/or **PyNaCl** (the Python library) for signing and signature verification. The design uses PyNaCl on the connected/appliance side with the minisign binary as the documented interop path. Both are trivial to install; neither goes near the frozen `offline.py`.
- A minisign keypair generated **outside the repo** (e.g. `~/.config/modelferry/`), never committed. `minisign -G -p pub.key -s sec.key`, or the PyNaCl equivalent.
- gitleaks or trufflehog for the pre-commit secret scanner.

That is the whole resource list for 0.2.0. No GPU, no cloud, no new hardware.

## Dev machine (whole build)

- CPU workstation or laptop, 32GB RAM comfortable, 16GB works for small local test models.
- Linux or Windows with WSL2. Docker required (the air-gap harness uses `docker run --network none`).
- Disk is the real constraint from Phase 1 on. Models, wheelhouses, images, chunk copies, delta working sets add up. Budget 500GB–1TB free. An external NVMe SSD is a cheap way to get there.

Footprint math so the disk number is not a guess:
- Qwen2.5-0.5B ≈ 1GB, 7B ≈ 15GB, 14B ≈ 29GB, 72B ≈ 145GB (FP16).
- vLLM image ≈ 8–10GB. Wheelhouse ≈ 3–6GB.
- The CAS holds a second copy of chunks, and delta work needs headroom. Plan ~2× the largest model you test locally, plus images and wheelhouses.

Windows note carried from the 0.1.0 build: keep the repo off any cloud-synced (OneDrive) path, and set `UV_LINK_MODE=copy` permanently, or uv hits hardlink/file-lock errors mid-build. Both are already done as of the 0.2.0 branch.

## GPU for the serve-proofs (Phase 1 and the final run only)

NVIDIA GPU with enough VRAM for the served model:
- 7B FP16 ≈ 14GB — fits a 16GB card or larger.
- 14B FP16 ≈ 29GB — needs one 48GB card (A6000, A40) or two 24GB cards, or a 4-bit AWQ/GPTQ variant at ~10GB on a single 24GB card.
- 72B FP16 ≈ 145GB — two 80GB cards, or a 4-bit variant at ~40GB on one A100 80GB.

Practical plan:
- A local 24GB card (RTX 3090/4090) covers 7B FP16 and quantized 14B at zero marginal cost.
- Rent for bigger proofs: RunPod, Lambda, Vast.ai. Rough on-demand: A6000 48GB ~0.5–0.8/hr, A100 80GB ~1.5–2.5/hr, H100 higher. Verify current pricing when booking.
- You need the GPU only a few hours per relevant phase. The serve-proof is not long-running.

Driver note the plan depends on: whatever host serves the model must have an NVIDIA driver compatible with the bundled CUDA 12.4 userspace (roughly 550+). modelferry bundles the CUDA libraries and the server image, not the kernel driver. Rented instances come with a driver preinstalled. This is exactly why Phase 1 ships a driver-compatibility matrix and an older-CUDA fallback bundle (task 1.9) — a real pilot host often runs a frozen old driver.

## USB and FAT32 media

Already validated physically on a 15GB and a 65GB model during 0.1.0. For the full matrix:
- Two USB3 drives, 64GB+. Format one FAT32, one exFAT or NTFS.
- The FAT32 drive is not optional. Some locked-down environments mandate it, and its 4GiB per-file limit is exactly why the bundle chunks.

For CI and testing without a physical drive, a FAT32 loopback image on Linux/WSL2:
```bash
dd if=/dev/zero of=usb.img bs=1M count=65536
mkfs.vfat -F 32 usb.img
sudo mkdir -p /mnt/usb && sudo mount -o loop usb.img /mnt/usb
```
This lets the e2e FAT32 path run in CI without hardware.

## Air-gap simulation

Three levels, cheapest first:
- CI/unit: `docker run --network none` for every inside-gap step. The Phase 0 harness, enough to enforce the no-network claim continuously.
- Realistic demo: a VM with no NIC, or a second machine in airplane mode, as the isolated node. Move a bundle in on USB, install the appliance, run the loop. This is the design-partner demo.
- Two-machine: a connected packer and a truly disconnected node with a USB drive as the only bridge. Closest to a customer environment.

For diode/one-way tests you do not need a physical diode. Model it as a strict one-way copy: inventory export is the only outward artifact, the delta the only inward one, and the reconciliation manifest proves completeness with no back-channel. Enforce the direction in the test so a design needing a round-trip fails.

## Accounts and services (all free tiers)

- Hugging Face account and access token (for gated repos like Llama/Gemma, and to test license classification and refusal). Qwen is ungated.
- Docker Hub, to pull public server images like `vllm/vllm-openai`.
- GitHub, for the repo and Actions CI. Free runners are CPU-only, which covers everything except the serve-proof.
- No cloud provider account required if you rent GPU from RunPod or Vast.

## Software toolchain (all free and open source)

- Python 3.11, uv, Docker.
- **Signing (Phase 0): minisign, PyNaCl, gitleaks/trufflehog.**
- Postgres 16, Redis, MinIO (Phase 2 on).
- Django, DRF, Celery (Phase 2 on).
- zot (embedded OCI registry) (Phase 2).
- syft (SBOM), trivy/grype (image scan), pip-audit/osv-scanner (dep scan) (Phase 3).
- cosign (Phase 3 enterprise signing).
- zstd (inventory compression), age/libsodium (optional media encryption) (Phase 4).
- celerypeek, your own tool, for worker observability (Phase 5).

Snapshot the vulnerability databases for the scanners on the connected side and refresh them there. The scanners never run inside the gap.

## Test model matrix

Pick models that each prove something specific:
- Qwen2.5-0.5B-Instruct (~1GB) — pipeline correctness and CI speed; loads on CPU, so it runs the offline reconstruction test without a GPU.
- Qwen2.5-7B-Instruct (~15GB) — first real serve on a modest GPU. Already validated for packing/transfer in 0.1.0.
- Qwen2.5-14B-Instruct (~29GB) — the headline demo and proposal spec.
- Qwen2.5-72B-Instruct (~145GB) — scale proof for bundling, transfer, delta. Already validated for packing/transfer in 0.1.0. Serve via a 4-bit variant if GPU-limited.
- An AWQ/GPTQ 4-bit variant — the quantized path, smaller footprint.
- A GGUF variant with a llama.cpp server image — proves the engine is not tied to vLLM.
- A gated Llama or Gemma variant — HF-token handling and policy classification/refusal of a restrictive license.

## Which resource each phase needs

- **Phase 0, signing (0.2.0): laptop only. No GPU, no cloud, no new hardware.**
- Phase 1, closure engine (0.3.0): laptop for build and reconstruction; GPU for the serve and wheelhouse-install tests. A few rented hours, or a local 24GB card for 7B and quantized 14B.
- Phase 2, registry (0.4.0): laptop. Isolated-pull tests use `--network none` containers, no GPU.
- Phase 3, trust/compliance: laptop. Scanners and SBOM tools are CPU-only.
- Phase 4, delta/transport: laptop. FAT32 loopback for the media path.
- Phase 5, packaging/pilot (1.0): laptop for clean-room install; GPU for the final e2e serve; a physical USB drive and an isolated VM or second machine for the design-partner demo.

## Cost estimate

Part-time build, rough monthly:
- GPU rental: 10–30 hours a month, ~20–80 depending on the card, and **zero until Phase 1**. Zero throughout if you have a local 24GB card and stick to 7B and quantized variants.
- USB drives: one-time 20–40 (already owned from 0.1.0).
- External SSD if disk is tight: one-time 60–120 for a terabyte.
- Everything else: 0. Open-source software, free-tier accounts, free CI runners.

The build cost is essentially your time plus a small, intermittent GPU line that does not start until Phase 1.

## The design-partner conversation (do this during Phase 0–1)

This is not a resource you buy, it is the highest-leverage thing in the whole plan, and it runs in parallel with the signing build. The plan's value depends on turning one real disconnected environment into a named design partner. Ask five questions, in one fifteen-minute conversation, of someone who runs or buys for an air-gapped environment (the civil-affairs relationship and the Technology Track air-gap access are the assets here):

1. **How do you get an open model into your disconnected environment today?** (Listen for: manual, painful, ad hoc. That pain is the whole thesis. If they say "we don't, we're not allowed to run open models," that is also a critical answer.)
2. **When a model is inside, how do you prove to your security/compliance people that it is what it claims to be, and that nothing was tampered with?** (Listen for: they can't, or it's a signature on a spreadsheet. This is what signing and the manifest sell.)
3. **When a CVE or a license problem hits a model you've already deployed, how do you find out, and what do you do?** (Listen for: no process. This is the compliance/revocation story.)
4. **If a tool packaged a model with everything to run it offline, signed, with an SBOM and a compliance report your auditor accepts, would that save you real time or unblock something you currently can't do?** (This is the direct product test. A shrug means rethink; a "when can I try it" means you have a design partner.)
5. **Would you pilot it in your environment?** (The actual close. A named yes here is worth more than any phase of code.)

Do not build Phase 2 (the registry appliance) before you have a real answer to 4 and 5 from at least one person. The signing release (0.2.0) and the closure release (0.3.0) are worth shipping regardless — they help the CLI users you already have. The appliance is the bet that needs a pilot signal first.
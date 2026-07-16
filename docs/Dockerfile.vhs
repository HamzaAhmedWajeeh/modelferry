# Renders docs/demo.tape into docs/demo.gif. The official vhs image bundles ttyd
# and ffmpeg but has no modelferry, and the tape calls it as a bare command, so
# this adds Python and installs the local project. See docs/demo.md to regenerate.
FROM ghcr.io/charmbracelet/vhs

# Debian 13 base already has python3; add pip (PEP 668 needs the override below).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY pyproject.toml README.md LICENSE /work/
COPY src /work/src
COPY docs /work/docs
RUN python3 -m pip install --break-system-packages /work

# vhs is the base image's entrypoint; pass the tape path at run time:
#   docker run <image> docs/demo.tape

# R16 sandbox image: what agent, install, test and lint commands run inside during LIVE runs.
# Build: make sandbox-image. Each command runs in a fresh `docker run --rm` (see sandbox.py)
# with only the task worktree mounted. Tests/lint get --network none.
FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      git git-lfs python3 python3-dev python3-pip python3-venv python-is-python3 build-essential \
      ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# C libraries that cgo / -sys crates commonly link against, so those repos' tests can build
# offline (e.g. Go audio players need ALSA, FLAC, mpg123, Ogg/Vorbis via pkg-config).
RUN apt-get update && apt-get install -y --no-install-recommends \
      pkg-config libssl-dev libasound2-dev libflac-dev libmpg123-dev libogg-dev libvorbis-dev \
 && rm -rf /var/lib/apt/lists/*

# 2D graphics stacks: Cairo/Pango for Python renderers (manim's pycairo and manimpango build
# from source) and node-canvas, which falls back to building against them plus image codecs.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Agent CLIs. Authenticate with env vars passed per run (ANTHROPIC_API_KEY or
# CLAUDE_CODE_OAUTH_TOKEN, OPENAI_API_KEY): host keychain logins do not reach the container.
RUN npm install -g @anthropic-ai/claude-code @openai/codex

# bun for repos whose lockfile is bun.lock (pinned; the npm package ships the binary)
RUN npm install -g bun@1.3.0

# pnpm and yarn for repos with pnpm-lock.yaml / yarn.lock (npm cannot install `workspace:` deps), via
# corepack so each repo gets the pnpm its `packageManager` field pins (fabric.js needs pnpm 10,
# galacean/engine pnpm 9). The install step downloads it into COREPACK_HOME in the worktree
# (sandbox.py), so the offline test step finds it.
RUN corepack enable pnpm yarn

# npm ci can skip this platform's native optional packages (npm/cli#4828); the engine runs this
# after npm installs (toolchain.py) to add exactly the missing ones without touching the lockfile.
COPY container/tithe-fix-optional.js /opt/tithe/fix-optional.js

# Headless browser tests (Playwright, vitest browser mode, Puppeteer). Only the system libraries
# live here: `install-deps` needs root, which the per-run --user lacks. The browser itself must
# match each repo's playwright version, so the engine's install step fetches it into the
# worktree (PLAYWRIGHT_BROWSERS_PATH, see sandbox.py). Debian's chromium is the fallback for
# runners that take an executable path (Puppeteer, Karma) instead of a Playwright build.
RUN npx -y playwright@1.64.0 install-deps chromium \
 && apt-get update && apt-get install -y --no-install-recommends chromium fonts-liberation \
 && rm -rf /var/lib/apt/lists/* /root/.npm
ENV CHROME_BIN=/usr/bin/chromium PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# Software GPU for wgpu/Vulkan graphics repos (linebender/vello's Linux CI uses the same):
# Mesa's lavapipe Vulkan driver and llvmpipe GL/EGL, so GPU render tests find an adapter.
# From bookworm-backports: bookworm's Mesa 22.3 lavapipe lacks shader float16, which
# vello's shaders require.
RUN echo "deb http://deb.debian.org/debian bookworm-backports main" \
      > /etc/apt/sources.list.d/backports.list \
 && apt-get update && apt-get install -y --no-install-recommends -t bookworm-backports \
      mesa-vulkan-drivers libvulkan1 libegl-mesa0 libgl1-mesa-dri \
 && rm -rf /var/lib/apt/lists/*

# Rust toolchain for cargo fixtures/repos, installed system-wide so any --user can use it.
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --component clippy \
 && chmod -R a+rwX /opt/rustup /opt/cargo

# Go from go.dev, pinned and checksum-verified: the Debian golang-go package is too old to
# even parse modern go.mod `go 1.2x.y` lines. GOTOOLCHAIN=auto lets a newer `go`/`toolchain`
# line fetch its toolchain during the networked install step; it lands in GOMODCACHE inside
# the worktree (sandbox.py), so the offline test step finds it.
ARG GO_VERSION=1.27.2
ARG GO_SHA256_AMD64=ecbadb99091a3f46e31f5f934b068b1864eafa7995211b39eaddf76996045fe5
ARG GO_SHA256_ARM64=94f3e30b8e374bc285e7dadc11e0865726b9bc6e85b841ccceaabc0214c6b7c8
ARG TARGETARCH
RUN set -eu; arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64) sha="$GO_SHA256_AMD64" ;; \
      arm64) sha="$GO_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-${arch}.tar.gz"; \
    echo "$sha  /tmp/go.tgz" | sha256sum -c -; \
    tar -C /usr/local -xzf /tmp/go.tgz; rm /tmp/go.tgz
ENV PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=auto

# envtest control plane (etcd, kube-apiserver, kubectl) for Kubernetes controller repos:
# controller-runtime's envtest looks in /usr/local/kubebuilder/bin when KUBEBUILDER_ASSETS is
# unset, and test runs have no network to download it themselves.
ARG ENVTEST_K8S_VERSION=1.37.x
ARG SETUP_ENVTEST_VERSION=latest
RUN set -eu; GOBIN=/tmp/envtest-bin GOPATH=/tmp/envtest-gopath GOCACHE=/tmp/envtest-cache \
      go install "sigs.k8s.io/controller-runtime/tools/setup-envtest@${SETUP_ENVTEST_VERSION}"; \
    assets="$(/tmp/envtest-bin/setup-envtest use "$ENVTEST_K8S_VERSION" --bin-dir /tmp/envtest-assets -p path)"; \
    mkdir -p /usr/local/kubebuilder/bin; cp "$assets"/* /usr/local/kubebuilder/bin/; \
    mv /usr/local/kubebuilder/bin/kube-apiserver /usr/local/kubebuilder/kube-apiserver.real; \
    printf '#!/bin/sh\nexec /usr/local/kubebuilder/kube-apiserver.real --advertise-address=192.0.2.1 "$@"\n' \
      > /usr/local/kubebuilder/bin/kube-apiserver; \
    chmod -R a+rx /usr/local/kubebuilder; rm -rf /tmp/envtest-*
# With --network none there is no default route, so kube-apiserver cannot pick an advertise
# address and refuses loopback; the wrapper gives it 192.0.2.1 (TEST-NET-1: published only,
# never bound or reachable).
ENV KUBEBUILDER_ASSETS=/usr/local/kubebuilder/bin

# HOME for the per-run --user (sandbox.py sets HOME=/tmp/home); tools that write to ~ need it
RUN mkdir -m 1777 /tmp/home

# Python dependencies go into /work/.tithe-venv (created by the engine), so they survive
# between the fresh containers used for install and test steps.
WORKDIR /work

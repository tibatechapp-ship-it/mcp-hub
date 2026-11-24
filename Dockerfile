# syntax=docker/dockerfile:1.6
# Pinned to the oldest line that still gets security updates *and* has a
# published base image. Dependabot's python:3.15-slim bump is closed: 3.15 is
# not released, so that tag resolves to a pre-release Debian snapshot with no
# patch stream behind it. 3.13 is the current stable line.
FROM python:3.15-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 mcphub

# Pull the Debian security updates published after the base image snapshot.
# The Trivy gate in CI scans this layer; an unpatched base is a red build.
# The distro also ships an outdated python3-setuptools in dist-packages (not on
# the app's sys.path — pip installs to /usr/local). Drop the stale copy so the
# image carries exactly one, current setuptools.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get purge -y python3-setuptools \
    && rm -rf /usr/lib/python3/dist-packages/setuptools* \
              /usr/lib/python3/dist-packages/pkg_resources* \
              /usr/lib/python3/dist-packages/_distutils_hack \
              /usr/lib/python3/dist-packages/distutils-precedence.pth \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY registry ./registry
# Keep the bundled packaging tools current — they ship in the runtime image
# and Trivy audits them like any other package.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir ".[api]" \
    # pip ships a CycloneDX bill-of-materials of its vendored packages. It is
    # stale by design (setuptools 70.3.0) and scanners read it as the image's
    # SBOM, hiding the real filesystem. Drop it.
    && rm -f /usr/local/lib/python3*/site-packages/pip/_vendor/bom.cdx.json

RUN mkdir -p /data && chown mcphub:mcphub /data
VOLUME ["/data"]

USER mcphub
ENV MCP_HUB_DB=/data/mcp_hub.sqlite3

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

ENTRYPOINT ["mcp-hub"]
CMD ["--db", "/data/mcp_hub.sqlite3", "serve", "--host", "0.0.0.0", "--port", "8000"]

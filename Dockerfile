# syntax=docker/dockerfile:1.6
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 mcphub

# Pull the Debian security updates published after the base image snapshot.
# The Trivy gate in CI scans this layer; an unpatched base is a red build.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
# Keep pip itself current — the runtime image carries it, and Trivy audits it.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api]"

RUN mkdir -p /data && chown mcphub:mcphub /data
VOLUME ["/data"]

USER mcphub
ENV MCP_HUB_DB=/data/mcp_hub.sqlite3

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

ENTRYPOINT ["mcp-hub"]
CMD ["--db", "/data/mcp_hub.sqlite3", "serve", "--host", "0.0.0.0", "--port", "8000"]

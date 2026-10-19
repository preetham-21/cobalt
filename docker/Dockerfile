# cobalt dev/toolchain image: Python 3.12, GnuCOBOL 3.x, OpenJDK 21, Maven.
#
# Ubuntu 24.04 is chosen because its archive ships all three toolchains at
# the versions the project needs (python3 3.12, gnucobol3 3.x,
# openjdk-21). Pin the base by digest once the image is first built:
#   docker inspect --format '{{index .RepoDigests 0}}' ubuntu:24.04
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    COBALT_IN_DOCKER=1 \
    COBALT_ROOT=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        make \
        tini \
        gnucobol3 \
        openjdk-21-jdk-headless \
        maven \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# The base image already has an unprivileged 'ubuntu' user (uid 1000).
RUN python3 -m venv /opt/venv && chown -R ubuntu:ubuntu /opt/venv
USER ubuntu
WORKDIR /app

# Install dependencies first so source edits do not invalidate this layer.
# The editable install points at /app/src, which docker compose bind-mounts.
COPY --chown=ubuntu:ubuntu pyproject.toml README.md ./
COPY --chown=ubuntu:ubuntu src ./src
RUN pip install --no-cache-dir --upgrade pip==25.0.1 \
    && pip install --no-cache-dir -e ".[dev]"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]

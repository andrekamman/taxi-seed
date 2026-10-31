# syntax=docker/dockerfile:1
FROM python:3.12-slim

# sqlcmd: needed for Ola's MaintenanceSolution.sql, which is GO-batch separated
# and can only be run by a client that understands GO.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends mssql-tools18 \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/mssql-tools18/bin:${PATH}"

# Child stdout is a pipe under `docker compose up`, so Python block-buffers it
# and a progress line can sit unflushed for minutes. This applies to taxi-seed's
# other CLIs too -- they inherit the environment through stages.run.
ENV PYTHONUNBUFFERED=1

WORKDIR /app
# The build context is this repo, so taxi-seed is installed from the source the
# image was built from rather than resolved from PyPI. The image can no longer
# drift from its own source, and it no longer needs a published release to exist.
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES ./
COPY shared/ ./shared/
COPY download/ ./download/
COPY schema-drift/ ./schema-drift/
COPY normalize/ ./normalize/
COPY load/ ./load/
COPY pipeline/ ./pipeline/
COPY mssql/ ./mssql/
# hatch-vcs derives the version from git, and .git is deliberately out of the
# build context -- including it would invalidate every cached layer on each
# commit. Without help, fallback-version makes the image report 0.0.0, so it
# cannot say what it was built from. Compose passes the real version through
# this arg; leave it unset and the fallback applies rather than the build failing.
#
# The GLOBAL variable, not SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TAXI_SEED: the
# per-distribution form is silently ignored under hatch-vcs (verified -- the env
# var is set and the install still produces 0.0.0). Scoped to this RUN rather
# than ENV so it cannot affect anything else built in this image, and only
# exported when non-empty, since an empty value is not the same as unset.
ARG TAXI_SEED_VERSION
RUN if [ -n "$TAXI_SEED_VERSION" ]; then \
      export SETUPTOOLS_SCM_PRETEND_VERSION="$TAXI_SEED_VERSION"; \
    fi; \
    pip install --no-cache-dir .

# Pristine mappings, copied from this repo instead of curled from
# raw.githubusercontent.com at a pinned ref -- they live here now, so the image
# cannot disagree with the source it was built from. preflight copies them onto
# the volume on first run and never overwrites, so drift amendments survive the
# container.
RUN mkdir -p /opt/taxi-seed/mappings \
 && cp /app/normalize/mappings/*.yaml /opt/taxi-seed/mappings/

# Bake the vendored DuckDB mssql extension so the container needs no network for
# it at runtime. taxi-load would fetch it on first use otherwise, which is a
# download inside a multi-hour run rather than at build time, and it would also
# re-fetch on every fresh container.
ENV TAXI_EXTENSION_DIR=/opt/taxi-seed/extensions
RUN taxi-vendor-extension --root "$TAXI_EXTENSION_DIR"

# CWD is the data dir: `taxi-normalize` resolves mappings from the RELATIVE path
# normalize/mappings/<type>.yaml, so this is load-bearing, not cosmetic.
WORKDIR /data
ENTRYPOINT ["taxi-mssql"]

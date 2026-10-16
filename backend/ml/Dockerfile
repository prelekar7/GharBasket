# The ML service for a Hugging Face Space (ADR-0025): uvicorn on port 7860 with osrm-routed as a
# second process, the Mumbai Metropolitan Region road network baked in at build time (a free Space
# has no persistent disk). Build locally with `just ml-lock && docker build -t gharbasket-ml backend/ml`
# (requirements.lock is generated from uv.lock, by deploy-ml.yml in CI).
#
# Stage 1 clips the OpenStreetMap extract to the MMR box and builds the OSRM (MLD) network, as
# infra/osrm/prepare.sh does on a laptop. Stage 2 is the OSRM runtime image plus a uv-managed
# Python and this package, installed from the locked versions: the two processes share one
# container (handbook 12) and run as the non-root user 1000, as on a Space.
#
# Every input is pinned except the OSM extract: Geofabrik publishes only "latest" under a stable
# name, so the date of the data that was baked in is recorded in /data/mmr.osm-date and logged by
# start.sh at startup. The base images are pinned by their multi-arch index digests (amd64 and
# arm64); bump the tag and the digest together.

ARG OSRM_IMAGE=ghcr.io/project-osrm/osrm-backend:v26.10.0-debian@sha256:55dbfd47d984c2cacd64e32901b4321c86b5ec761438e69aa053dc78698a786f
ARG DEBIAN_IMAGE=debian:stable-slim@sha256:5bc3287b25407c965a30f38e32603dc253a3869e1b12a21ac09bfc27fd8b13ce
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.23
ARG PYTHON_VERSION=3.13.16
# OpenStreetMap © OpenStreetMap contributors (ODbL), Geofabrik's western-zone extract (~220 MB).
ARG OSM_URL=https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf
# west,south,east,north: Mumbai, Thane, Navi Mumbai and Panvel.
ARG MMR_BBOX=72.75,18.85,73.30,19.35

FROM ${UV_IMAGE} AS uv

FROM ${DEBIAN_IMAGE} AS clip
ARG OSM_URL
ARG MMR_BBOX
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates osmium-tool >/dev/null \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /data
RUN curl --fail --location --silent --show-error --output western-zone.osm.pbf "$OSM_URL" \
    && osmium extract --bbox "$MMR_BBOX" --strategy complete_ways -o mmr.osm.pbf western-zone.osm.pbf \
    && rm western-zone.osm.pbf
# The newest edit in the clipped data: the "as of" date of the road network.
RUN osmium fileinfo --extended --get data.timestamp.last mmr.osm.pbf > mmr.osm-date

FROM ${OSRM_IMAGE} AS network
WORKDIR /data
COPY --from=clip /data/mmr.osm.pbf /data/mmr.osm.pbf
RUN osrm-extract -p /opt/car.lua /data/mmr.osm.pbf \
    && osrm-partition /data/mmr.osrm \
    && osrm-customize /data/mmr.osrm \
    && rm /data/mmr.osm.pbf

FROM ${OSRM_IMAGE}
ARG OSM_URL
ARG PYTHON_VERSION
LABEL org.opencontainers.image.source="https://github.com/prelekar7/GharBasket" \
      org.gharbasket.osm.source="${OSM_URL}" \
      org.gharbasket.osm.date-file="/data/mmr.osm-date"
# Hugging Face runs a Space's container as user 1000; the image does the same.
RUN useradd --create-home --uid 1000 app
ENV APP_ENV=prod \
    OSRM_URL=http://127.0.0.1:5000 \
    HOME=/home/app \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/opt/venv/bin:$PATH"
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
# The interpreter and the locked dependencies first (they change least), then this package without
# resolving anything again. /opt stays root-owned and read-only for the app user; the bytecode is
# compiled here because the app user cannot write __pycache__ there.
COPY requirements.lock ./
RUN uv python install --no-bin "$PYTHON_VERSION" \
    && uv venv /opt/venv --python "$PYTHON_VERSION" \
    && uv pip install --python /opt/venv/bin/python -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --python /opt/venv/bin/python --no-deps .
# osrm-extract writes some files (mmr.osrm.fileIndex) as 0600, so the app user must own them.
COPY --from=network --chown=app:app /data/mmr.osrm* /data/
COPY --from=clip /data/mmr.osm-date /data/mmr.osm-date
COPY start.sh /app/start.sh
RUN chmod 755 /app/start.sh && chown -R app:app /app
USER app
EXPOSE 7860
CMD ["/app/start.sh"]

# The ML service for a Hugging Face Space (ADR-0025): uvicorn on port 7860 with osrm-routed as a
# second process, the Mumbai Metropolitan Region road network baked in at build time (a free Space
# has no persistent disk). Build locally with `docker build -t gharbasket-ml backend/ml`.
#
# Stage 1 clips the OpenStreetMap extract to the MMR box and builds the OSRM (MLD) network, as
# infra/osrm/prepare.sh does on a laptop. Stage 2 is the OSRM runtime image plus a uv-managed
# Python 3.13 and this package: the two processes share one container (handbook 12).

ARG OSRM_IMAGE=ghcr.io/project-osrm/osrm-backend:v26.10.0-debian
# OpenStreetMap © OpenStreetMap contributors (ODbL), Geofabrik's western-zone extract (~220 MB).
ARG OSM_URL=https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf
# west,south,east,north: Mumbai, Thane, Navi Mumbai and Panvel.
ARG MMR_BBOX=72.75,18.85,73.30,19.35

FROM debian:stable-slim AS clip
ARG OSM_URL
ARG MMR_BBOX
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates osmium-tool >/dev/null \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /data
RUN curl --fail --location --silent --show-error --output western-zone.osm.pbf "$OSM_URL" \
    && osmium extract --bbox "$MMR_BBOX" --strategy complete_ways -o mmr.osm.pbf western-zone.osm.pbf \
    && rm western-zone.osm.pbf

FROM ${OSRM_IMAGE} AS network
WORKDIR /data
COPY --from=clip /data/mmr.osm.pbf /data/mmr.osm.pbf
RUN osrm-extract -p /opt/car.lua /data/mmr.osm.pbf \
    && osrm-partition /data/mmr.osrm \
    && osrm-customize /data/mmr.osrm \
    && rm /data/mmr.osm.pbf

FROM ${OSRM_IMAGE}
LABEL org.opencontainers.image.source="https://github.com/prelekar7/GharBasket"
ENV APP_ENV=prod \
    OSRM_URL=http://127.0.0.1:5000 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/opt/venv/bin:/root/.local/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv python install 3.13 \
    && uv venv /opt/venv --python 3.13 \
    && uv pip install --python /opt/venv/bin/python --no-cache . \
    && rm -rf /root/.cache
COPY --from=network /data/mmr.osrm* /data/
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
EXPOSE 7860
CMD ["/app/start.sh"]

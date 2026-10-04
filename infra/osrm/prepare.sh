#!/usr/bin/env bash
# Build the OSRM road network for the Mumbai Metropolitan Region (spike S-3, M7 routing):
#   just osrm-prepare      (about 5 minutes and 1 GB of disk the first time)
#
# 1. Download Geofabrik's western-India extract (~220 MB), once.
# 2. Clip it to the MMR box (Mumbai, Thane, Navi Mumbai, Panvel) with osmium (Debian package,
#    in a throwaway container, so nothing is installed on this machine).
# 3. osrm-extract (car profile) → osrm-partition → osrm-customize (MLD), for `just up-routing`.
# Everything lives in infra/osrm/data/ (git-ignored).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
data="$here/data"
osrm_image="ghcr.io/project-osrm/osrm-backend:v26.10.0-debian"
source_url="https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf"
# west,south,east,north: Mumbai to Panvel and Thane (OpenStreetMap © OpenStreetMap contributors, ODbL)
bbox="72.75,18.85,73.30,19.35"

mkdir -p "$data"
if [ ! -f "$data/western-zone.osm.pbf" ]; then
  echo "Downloading $source_url"
  curl --fail --location --output "$data/western-zone.osm.pbf.part" "$source_url"
  mv "$data/western-zone.osm.pbf.part" "$data/western-zone.osm.pbf"
fi

if [ ! -f "$data/mmr.osm.pbf" ]; then
  echo "Clipping to the MMR box $bbox"
  docker run --rm -v "$data:/data" debian:stable-slim sh -c \
    "apt-get update -qq && apt-get install -y -qq osmium-tool >/dev/null && \
     osmium extract --bbox $bbox --strategy complete_ways --overwrite \
       -o /data/mmr.osm.pbf /data/western-zone.osm.pbf"
fi

echo "Building the car network (extract, partition, customize)"
docker run --rm -v "$data:/data" "$osrm_image" osrm-extract -p /opt/car.lua /data/mmr.osm.pbf
docker run --rm -v "$data:/data" "$osrm_image" osrm-partition /data/mmr.osrm
docker run --rm -v "$data:/data" "$osrm_image" osrm-customize /data/mmr.osrm
echo "Done. Start it with: just up-routing   (http://localhost:5050)"

# OSRM: road distances for routing (M7, M10 M-F)

A self-hosted [OSRM](https://project-osrm.org/) server gives road distances and travel times. The van
routing (OR-Tools) uses them as its matrix, so it pays for no Google Maps billing (TDD §maps).

```
just osrm-prepare   # once: download, clip and build the network (about 3 minutes, 400 MB on disk)
just up-routing     # start it on http://localhost:5050
curl "http://localhost:5050/route/v1/driving/73.0690,19.0300;73.1185,19.0005?overview=false"
```

- **Data:** OpenStreetMap © OpenStreetMap contributors (ODbL), from Geofabrik's `western-zone` extract (about 220 MB).
- **Area:** the extract is clipped with osmium to the Mumbai Metropolitan Region box `72.75,18.85,73.30,19.35` (Mumbai, Thane, Navi Mumbai and Panvel).
- **Profile:** the stock `car.lua`. OSRM has no live traffic, so callers scale its times (spike S-3 uses × 1.5 for a loaded e-rickshaw in the morning).
- **Algorithm:** MLD (`osrm-partition` + `osrm-customize`). It can later take speed updates without a full rebuild.
- **Image:** `ghcr.io/project-osrm/osrm-backend:v26.10.0-debian`, pinned in `prepare.sh` and in `infra/compose/docker-compose.yml`. Bump both together, then rerun `just osrm-prepare`, because the data files belong to one OSRM version.
- **Cost:** the server uses about 100 MB of RAM for the region. Building the network peaks at about 120 MB.
- **Refresh:** delete `data/` and rerun `just osrm-prepare` to pick up new roads (monthly is enough).

Everything lives in `infra/osrm/data/` (git-ignored). Hosting for staging is still open (ADR-0020). It is small enough to sit next to the ML service.

# S-3: van routing with OR-Tools and OSRM

Throwaway spike code (excluded from lint). The findings are in [docs/spikes/S-3-routing.md](../../docs/spikes/S-3-routing.md).

```
just osrm-prepare && just up-routing      # once: the Mumbai-region road network on :5050
uv run --with ortools==9.15.6755 python spikes/s3-routing/route.py            # 50 societies (~3 min)
uv run --with ortools==9.15.6755 python spikes/s3-routing/route.py --scale    # also 100 and 200 (~10 min)
```

- **Instance:** synthetic and seeded. It uses the five seed societies plus random points in Kharghar, Kamothe, Kalamboli and Panvel, each snapped to a road.
- **Scoring:** every method is scored by the same `evaluate()`, which replays the routes on the OSRM matrix and checks capacity, the donation pairing and the return time.
- **Outputs:**
  - `results.txt`: the run quoted in the note;
  - `routes.geojson`: the two-wave routes for 50 societies (open it in geojson.io).

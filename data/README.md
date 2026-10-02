# data

Owner: Prathamesh Relekar.

- `simulator/`: deterministic synthetic households, orders, festivals and price shocks. Writes through module services with `data_source='synthetic'`; ground truth goes to `data/sim_truth/` (git-ignored). Arrives in `feat/M10-simulator-v0`.
- `seeds/`: small hand-curated seed data (hubs, societies, catalog).

See `docs/handbook/05-data-architecture.md` (simulator section).

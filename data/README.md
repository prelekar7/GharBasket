# data

Owner: Prathamesh Relekar.

- `simulator/`: deterministic synthetic households and their weeks (`just sim`; see its README).
  - Writes through module services with `data_source='synthetic'`.
  - Ground truth goes to `data/sim_truth/` (git-ignored).
  - Orders, price shocks and churn come in later versions.
- `seeds/`: small hand-curated seed data (hubs, societies, catalog).

See `docs/handbook/05-data-architecture.md` (simulator section).

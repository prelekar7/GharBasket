"""GharBasket training, forecasting and data jobs (M10, ADR-0025)."""

# The API's modules import one another through their `api.py` facades. Loaded in M-number order,
# as the API and its tools load them, the cycles resolve; entered at `orders.api` (as the forecast
# job used to), `hub_ops.packing` needed `orders.api` half-imported and every job run crashed.
from gharbasket.modules.registry import load_modules

load_modules()

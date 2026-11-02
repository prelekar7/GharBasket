"""Outbox events published by M10 Intelligence gateway, named `intel.<event>`."""

# A hub's demand forecast is stored (the forecast job, M10 §3.6); M6 builds its procurement plan
# from `intel_gateway.api.hub_forecast(hub_id, delivery_date)` for each day listed.
# Payload: hub_id, delivery_dates [iso dates], model, data_source.
FORECAST_READY = "intel.forecast_ready"

# A model's forecast began to drift: its WAPE on a delivery day was more than the drift threshold
# worse than its backtest, and the previous scored day of the same model, hub and data source was
# not drifting (`monitor_drift`, M10 §6): once per episode, so a drift that lasts is announced
# once, and again only after a day without drift. M11 tells the admins.
# Payload: model, version, hub_id, data_source, metric, live, backtest, delta, window_end (the
# delivery day the episode began on).
DRIFT_DETECTED = "intel.drift_detected"

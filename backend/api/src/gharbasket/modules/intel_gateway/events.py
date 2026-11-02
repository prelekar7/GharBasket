"""Outbox events published by M10 Intelligence gateway, named `intel.<event>`."""

# A hub's demand forecast is stored (the forecast job, M10 §3.6); M6 builds its procurement plan
# from `intel_gateway.api.hub_forecast(hub_id, delivery_date)` for each day listed.
# Payload: hub_id, delivery_dates [iso dates], model, data_source.
FORECAST_READY = "intel.forecast_ready"

# A model scored worse on a delivery day than in its backtest by more than the drift threshold
# (`monitor_drift`, M10 §6): once per hub, data source and metric per run, for the newest such day.
# M11 tells the admins. Payload: hub_id, data_source, metric, live, backtest, delta, window_end
# (the drifting delivery day).
DRIFT_DETECTED = "intel.drift_detected"

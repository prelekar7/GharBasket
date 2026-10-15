"""Outbox events published by M10 Intelligence gateway, named `intel.<event>`."""

# A hub's demand forecast is stored (the forecast job, M10 §3.6); M6 builds its procurement plan
# from `intel_gateway.api.hub_forecast(hub_id, delivery_date)` for each day listed.
# Payload: hub_id, delivery_dates [iso dates], model, data_source.
FORECAST_READY = "intel.forecast_ready"

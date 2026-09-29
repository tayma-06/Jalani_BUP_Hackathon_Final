# Monitoring starter

Prometheus expects `backend:8080/metrics` on the same Docker network. Mount `prometheus.yml` and `rules.yml` into `/etc/prometheus/`. Grafana needs the two provisioning directories and the `dashboards/` directory at the paths used in provisioning. Use loopback host ports 9090 and 3001 for the demo.

These files are a starter; the app must instrument the metric names in plan.md. Validate rules with `promtool check rules` after pulling your pinned Prometheus image. Validate the dashboard in Grafana and capture a screenshot; JSON parsing alone does not prove that panels have data.

The included dashboard covers backend availability, actual simulator data availability, state age/staleness, service level, and memory. Add latency, errors, forecast error, confidence, decisions, and fuel inventory panels with the metric names actually emitted by the application. Do not invent series or fake nonzero values to make a panel look alive.

The provided `sim_allocation_failures` rule assumes the value is a monotonically increasing total within a run. Scope/reset it correctly when a simulator reset occurs; a simple gauge difference across runs is not a reliable failure rate.

Alerts are evaluated inside Prometheus. To send notifications outside the system, configure an Alertmanager route separately; these files do not send messages or emails.

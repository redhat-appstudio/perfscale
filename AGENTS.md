# AGENTS.md - Konflux Perf&Scale

## Purpose

This repo holds Grafana dashboards and operational tools for the Konflux Performance & Scale team.

## Repo layout

- `grafana/` - In-cluster Grafana dashboard JSONs (deployed via infra-deployments) and generic dashboards around Probe runs (deployd manually to <https://grafana.corp.redhat.com/dashboards?orgId=26>). After merging dashboard changes, update ref in infra-deployments repo with `update-infra-ref.sh <sha>`.
- `grafonnet-workdir/` - Jsonnet/Grafonnet sources for some dashboards. Build with `grafonnet-workdir/build.sh` that updates some dashboards in `grafana/`. Grafonnet vendor dependencies are committed; run `jb install` only when updating them.
- `tools/oomkill-and-crashloopbackoff-detector/` - Parallel OOMKilled/CrashLoopBackOff scanner across OpenShift clusters.
- `tools/tasks-and-steps-resource-analyzer/` - Extracts per-task/step Memory & CPU metrics from Prometheus and recommends resource limits.
- `cleanup-dashboard.sh` - Strips datasource fields from dashboard JSONs. Used when creating dashboard in Grafana UI and we want to persist in this git. Long term plan though is to have all the dashboards created by Jsonnet.
- `update-infra-ref.sh` - Updates infra-deployments kustomization ref to a commit SHA. Note it expects a sibling `../infra-deployments` checkout.

## Key technologies

- Grafana dashboards (JSON)
- Jsonnet + Grafonnet (dashboard-as-code)
- Python 3.9+ (tools)
- Bash (wrapper scripts)
- PromQL (Prometheus queries)
- OpenShift / Kubernetes (`oc` CLI)

## Testing

- Grafonnet dashboards: Make sure dashboards can be built with `grafonnet-workdir/build.sh`.
- Python code needs to pass `black` and `flake8`.
- Schell scripts need to pass `shellcheck`.
- No automated test suite exists in this repo.

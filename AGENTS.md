# AGENTS.md - Konflux Perf&Scale

## Purpose

This repo holds Grafana dashboards and operational tools for the Konflux Performance & Scale team.

## Repo layout

- `grafana/` - In-cluster Grafana dashboard JSONs (deployed via infra-deployments).
- `grafonnet-workdir/` - Jsonnet/Grafonnet sources for grafana.corp.redhat.com dashboards. Build with `grafonnet-workdir/build.sh`.
- `tools/oomkill-and-crashloopbackoff-detector/` - Parallel OOMKilled/CrashLoopBackOff scanner across OpenShift clusters.
- `tools/tasks-and-steps-resource-analyzer/` - Extracts per-task/step Memory & CPU metrics from Prometheus and recommends resource limits.
- `cleanup-dashboard.sh` - Strips datasource fields from dashboard JSONs.
- `update-infra-ref.sh` - Updates infra-deployments kustomization ref to a commit SHA.

## Key technologies

- Grafana dashboards (JSON)
- Jsonnet + Grafonnet (dashboard-as-code)
- Python 3.9+ (tools)
- Bash (wrapper scripts)
- PromQL (Prometheus queries)
- OpenShift / Kubernetes (`oc` CLI)

## Conventions

- Dashboard JSONs must have `datasource` fields removed before committing (use `cleanup-dashboard.sh`).
- Grafonnet-generated dashboards go in `grafonnet-workdir/generated/` and are copied to `grafana/dashboards/` by `build.sh`.
- After merging dashboard changes, update infra-deployments ref with `update-infra-ref.sh <sha>`.

## Build & test

- Grafonnet dashboards: install `jsonnet` (prefer go-jsonnet) and `jb`, then run `grafonnet-workdir/build.sh`.
- OOM detector: `python tools/oomkill-and-crashloopbackoff-detector/oc_get_ooms.py --help`
- Resource analyzer: `tools/tasks-and-steps-resource-analyzer/wrapper_for_promql_for_all_clusters.sh <days> --csv`
- No automated test suite exists in this repo.

## Common agent tasks

- Add or update a Grafana dashboard JSON in `grafana/dashboards/`.
- Modify Grafonnet sources in `grafonnet-workdir/src/` and regenerate with `build.sh`.
- Adjust resource analysis scripts or PromQL queries in `tools/`.
- Fix shell scripts (`cleanup-dashboard.sh`, `update-infra-ref.sh`).

## Pitfalls

- Never commit dashboard JSONs with `datasource` fields present.
- The `update-infra-ref.sh` script expects a sibling `../infra-deployments` checkout.
- Grafonnet vendor dependencies are committed; run `jb install` only when updating them.

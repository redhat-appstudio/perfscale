RHTAP Perf&Scale team repository
================================

Grafana dashboards
------------------

To add a dashboard, you just follow <https://github.com/redhat-appstudio/infra-deployments/blob/main/components/monitoring/README.md#teams-repository>.

In a nutshell:

1. Remove all `"datasource": {...}` from the dashboard json. You can use the script: `./cleanup-dashboard.sh grafana/dashboards/rhtap-performance.json`
2. Add dashboard json to `grafana/dashboards/` directory.
3. Add sections for it to `grafana/dashboard.yaml` and `grafana/kustomization.yaml` files.
4. Once merged, take git commit sha and create a infra-deployments PR to chage the commit sha in `components/monitoring/grafana/base/dashboards/performance/kustomization.yaml` file. You can do the change with `./update-infra-ref.sh <sha>`.

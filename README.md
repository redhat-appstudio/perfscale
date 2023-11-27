RHTAP Perf&Scale team repository
================================

Grafana dashboards
------------------

To add a dashboard, you just follow <https://github.com/redhat-appstudio/infra-deployments/blob/main/components/monitoring/README.md#teams-repository>.

In a nutshell:

1. Remove all `"datasource": {...}` from the dashboard json. Looks like this works fine: `cat grafana/dashboards/rhtap-performance.json | jq 'walk(if type == "object" then with_entries(select(.key | test("datasource") | not)) else . end)' > aaa.json; cp aaa.json grafana/dashboards/rhtap-performance.json`
2. Add dashboard json to grafana/dashboards/ directory.
3. Add sections for it to grafana/dashboard.yaml and grafana/kustomization.yaml files.
4. Once merged, take git commit sha and create a infra-deployments PR to chage the commit sha in `components/monitoring/grafana/base/performance/kustomization.yaml` file.

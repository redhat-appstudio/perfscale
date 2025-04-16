Konflux Perf&Scale team repository
==================================

In Konflux cluster Grafana dashboards
-------------------------------------

These dashboards appear in Grafana instanced deployed in Konflux clusters and are stored in `grafana/` in this repository.

To add a dashboard, you just follow <https://github.com/redhat-appstudio/infra-deployments/blob/main/components/monitoring/README.md#teams-repository>.

In a nutshell:

1. Remove all `"datasource": {...}` from the dashboard json. You can use the script: `./cleanup-dashboard.sh grafana/dashboards/rhtap-performance.json`
2. Add dashboard json to `grafana/dashboards/` directory.
3. Add sections for it to `grafana/dashboard.yaml` and `grafana/kustomization.yaml` files.
4. Once merged, take git commit sha and create a infra-deployments PR to chage the commit sha in `components/monitoring/grafana/base/dashboards/performance/kustomization.yaml` file. You can do the change with `./update-infra-ref.sh <sha>`.

Other dashboards
----------------

These dashboards live in <https://grafana.corp.redhat.com/> and are stored in `TODO` in this repository.

Our grafana.corp.redhat.com organization is "Konflux perf&scale" and it was initially requested in ticket [RITM2070980](https://redhat.service-now.com/surl.do?n=RITM2070980).

Data source we use for Probes dashboard is Perf Dept Integration Lab PostgreSQL DB schema requested in [INTLAB-459](https://issues.redhat.com/browse/INTLAB-459).

Access to our Grafana organization is guarded by these LDAP groups (ask owners of these groups to be added):

 * For admin access: <https://rover.redhat.com/groups/group/konflux-perfscale-grafanacorp-admins>
 * For user, read-only access: <https://rover.redhat.com/groups/group/konflux-perfscale-grafanacorp-users>

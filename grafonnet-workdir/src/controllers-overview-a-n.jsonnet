local grafonnet = import 'github.com/grafana/grafonnet/gen/grafonnet-latest/main.libsonnet';


// Just some shortcuts
local dashboard = grafonnet.dashboard;
local timeSeries = grafonnet.panel.timeSeries;


// Define "datasource" variable
local datasourceVar =
  grafonnet.dashboard.variable.datasource.new(
    'datasource',
    'prometheus',
  )
  + grafonnet.dashboard.variable.datasource.withRegex('.*-(appstudio)-.*')
  + grafonnet.dashboard.variable.custom.generalOptions.withLabel('datasource')
  + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
    'Description'
  )
  + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('prometheus-appstudio-ds');


// Define CPU usage related queries we will use
local cpuQueryCurrent(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="%s",pod=~"%s"}[$__rate_interval]))' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('{{pod}}');
local cpuQueryRequests(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'sum by (pod) (avg(kube_pod_container_resource_requests{resource="cpu", namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('REQUESTS');
local cpuQueryLimits(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'sum by (pod) (avg(kube_pod_container_resource_limits{resource="cpu", namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('LIMITS');
local cpuQueryFloatingAvg(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'avg_over_time(sum by (pod) (avg(rate(container_cpu_usage_seconds_total{namespace="%s",pod=~"%s"}[$__rate_interval])))[24h:])' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('24h AVG');

// Define memory usage related queries we will use
local memQueryCurrent(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'sum by (pod) (container_memory_working_set_bytes{namespace="%s",pod=~"%s",container=""})' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('{{pod}}');
local memQueryRequests(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'avg(sum by (pod) (kube_pod_container_resource_requests{resource="memory",namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('REQUESTS');
local memQueryLimits(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'avg(sum by (pod) (kube_pod_container_resource_limits{resource="memory",namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('LIMITS');
local memQueryFloatingAvg(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'avg_over_time(avg(sum by (pod) (container_memory_working_set_bytes{namespace="%s",pod=~"%s",container=""}))[24h:])' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('24h AVG');

// Define restarts count related query we will use
local restartsQueryCurrent(namespace, pod_rex) =
  grafonnet.query.prometheus.new(
    '$' + datasourceVar.name,
    'sum(kube_pod_container_status_restarts_total{namespace="%s",pod=~"%s"})' % [namespace, pod_rex],
  )
  + grafonnet.query.prometheus.withLegendFormat('restarts');


// Define panel functions
local myCPUPanel(namespace, pod_rex) =
  timeSeries.new('CPU usage for %s / %s' % [namespace, pod_rex])
  + timeSeries.queryOptions.withDatasource(
    type='prometheus',
    uid='$datasource',
  )
  + timeSeries.queryOptions.withTargets([
    cpuQueryCurrent(namespace, pod_rex),
    cpuQueryRequests(namespace, pod_rex),
    cpuQueryLimits(namespace, pod_rex),
    cpuQueryFloatingAvg(namespace, pod_rex),
  ])
  + timeSeries.standardOptions.withUnit('short')
  + timeSeries.standardOptions.withMin(0)
  + timeSeries.gridPos.withW(8)
  + timeSeries.gridPos.withH(8);

local myMemoryPanel(namespace, pod_rex) =
  timeSeries.new('Mem usage for %s / %s' % [namespace, pod_rex])
  + timeSeries.queryOptions.withDatasource(
    type='prometheus',
    uid='$datasource',
  )
  + timeSeries.queryOptions.withTargets([
    memQueryCurrent(namespace, pod_rex),
    memQueryRequests(namespace, pod_rex),
    memQueryLimits(namespace, pod_rex),
    memQueryFloatingAvg(namespace, pod_rex),
  ])
  + timeSeries.standardOptions.withUnit('bytes')
  + timeSeries.standardOptions.withMin(0)
  + timeSeries.gridPos.withW(8)
  + timeSeries.gridPos.withH(8);

local myRestartsPanel(namespace, pod_rex) =
  timeSeries.new('Restarts of %s / %s' % [namespace, pod_rex])
  + timeSeries.queryOptions.withDatasource(
    type='prometheus',
    uid='$datasource',
  )
  + timeSeries.queryOptions.withTargets([
    restartsQueryCurrent(namespace, pod_rex),
  ])
  + timeSeries.standardOptions.withUnit('short')
  + timeSeries.gridPos.withW(8)
  + timeSeries.gridPos.withH(8);

local myRow(namespace, pod_rex) =
  grafonnet.panel.row.new('Info about %s / %s' % [namespace, pod_rex])
  + grafonnet.panel.row.withPanels([
    myCPUPanel(namespace, pod_rex),
    myMemoryPanel(namespace, pod_rex),
    myRestartsPanel(namespace, pod_rex),
  ]);

// Finally dashboard
dashboard.new('Performance: Controllers resources and restarts overview (namespaces starting with a - n)')
+ dashboard.withDescription('Dashboard visualizes Konflux controllers basic resources use and health indicators: CPU, memory usage and number of restarts. Aim is to help spot unusual behavior.')
+ dashboard.time.withFrom(value='now-24h')
+ dashboard.withVariables([
  datasourceVar,
])
+ dashboard.withPanels(
  grafonnet.util.grid.makeGrid([
    myRow('application-service', 'application-service-controller-manager-[0-9a-z-]+'),
    myRow('appstudio-grafana', 'grafana-operator-controller-manager-v5-[0-9a-z-]+'),
    myRow('appstudio-monitoring', 'obo-prometheus-operator-[0-9a-z-]+'),
    myRow('appstudio-monitoring', 'observability-operator-[0-9a-z-]+'),
    myRow('appstudio-monitoring', 'perses-operator-[0-9a-z-]+'),
    myRow('build-service', 'build-service-controller-manager-[0-9a-z-]+'),
    myRow('etcd-shield', 'etcd-shield-[0-9a-z-]+'),
    myRow('external-secrets-operator', 'external-secrets-operator-[0-9a-z-]+'),
    myRow('external-secrets-operator', 'external-secrets-operator-cert-controller-[0-9a-z-]+'),
    myRow('external-secrets-operator', 'external-secrets-operator-webhook-[0-9a-z-]+'),
    myRow('image-controller', 'image-controller-controller-manager-[0-9a-z-]+'),
    myRow('image-rbac-proxy', 'dex-[0-9a-z-]+'),
    myRow('image-rbac-proxy', 'image-rbac-proxy-[0-9a-z-]+'),
    myRow('integration-service', 'integration-service-controller-manager-[0-9a-z-]+'),
    myRow('knative-eventing', 'eventing-controller-[0-9a-z-]+'),
    myRow('knative-eventing', 'eventing-webhook-[0-9a-z-]+'),
    myRow('knative-eventing', 'imc-controller-[0-9a-z-]+'),
    myRow('knative-eventing', 'imc-dispatcher-[0-9a-z-]+'),
    myRow('knative-eventing', 'job-sink-[0-9a-z-]+'),
    myRow('knative-eventing', 'mt-broker-controller-[0-9a-z-]+'),
    myRow('knative-eventing', 'mt-broker-filter-[0-9a-z-]+'),
    myRow('knative-eventing', 'mt-broker-ingress-[0-9a-z-]+'),
    myRow('konflux-kite', 'konflux-kite-[0-9a-z-]+'),
    myRow('konflux-kyverno', 'kyverno-admission-controller-[0-9a-z-]+'),
    myRow('konflux-kyverno', 'kyverno-background-controller-[0-9a-z-]+'),
    myRow('konflux-kyverno', 'kyverno-cleanup-controller-[0-9a-z-]+'),
    myRow('konflux-otel', 'open-telemetry-opentelemetry-collector-[0-9a-z-]+'),
    myRow('konflux-ui', 'dex-[0-9a-z-]+'),
    myRow('konflux-ui', 'proxy-[0-9a-z-]+'),
    myRow('mintmaker', 'mintmaker-controller-manager-[0-9a-z-]+'),
    myRow('multi-platform-controller', 'multi-platform-controller-[0-9a-z-]+'),
    myRow('multi-platform-controller', 'multi-platform-otp-server-[0-9a-z-]+'),
    myRow('namespace-lister', 'namespace-lister-[0-9a-z-]+'),
    myRow('notification-controller', 'notification-controller-controller-manager-[0-9a-z-]+'),
  ], panelWidth=8)
)

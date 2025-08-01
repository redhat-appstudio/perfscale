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
dashboard.new('Performance: Controllers resources and restarts overview')
+ dashboard.withDescription('Dashboard visualizes Konflux controllers basic resources use and health indicators: CPU, memory usage and number of restarts. Aim is to help spot unusual behavior.')
+ dashboard.time.withFrom(value='now-24h')
+ dashboard.withVariables([
  datasourceVar,
])
+ dashboard.withPanels(
  grafonnet.util.grid.makeGrid([
    myRow('application-service', 'application-service-controller-manager-[0-9a-z-]+'),
    myRow('build-service', 'build-service-controller-manager-[0-9a-z-]+'),
    myRow('etcd-shield', 'etcd-shield-[0-9a-z-]+'),
    myRow('external-secrets-operator', 'external-secrets-operator-controller-manager-[0-9a-z-]+'),
    myRow('image-controller', 'image-controller-controller-manager-[0-9a-z-]+'),
    myRow('integration-service', 'integration-service-controller-manager-[0-9a-z-]+'),
    myRow('jvm-build-service', 'hacbs-jvm-operator-[0-9a-z-]+'),
    myRow('knative-eventing', 'eventing-controller-[0-9a-z-]+'),
    myRow('knative-eventing', 'eventing-webhook-[0-9a-z-]+'),
    myRow('knative-eventing', 'imc-controller-[0-9a-z-]+'),
    myRow('knative-eventing', 'imc-dispatcher-[0-9a-z-]+'),
    myRow('knative-eventing', 'job-sink-[0-9a-z-]+'),
    myRow('knative-eventing', 'mt-broker-controller-[0-9a-z-]+'),
    myRow('knative-eventing', 'mt-broker-filter-[0-9a-z-]+'),
    myRow('knative-eventing', 'mt-broker-ingress-[0-9a-z-]+'),
    myRow('konflux-kyverno', 'kyverno-admission-controller-[0-9a-z-]+'),
    myRow('konflux-kyverno', 'kyverno-background-controller-[0-9a-z-]+'),
    myRow('konflux-kyverno', 'kyverno-cleanup-controller-[0-9a-z-]+'),
    myRow('kueue-external-admission', 'alert-mgr-kueue-admission-controller-manager-[0-9a-z-]+'),
    myRow('mintmaker', 'mintmaker-controller-manager-[0-9a-z-]+'),
    myRow('multi-platform-controller', 'multi-platform-controller-[0-9a-z-]+'),
    myRow('multi-platform-controller', 'multi-platform-otp-server-[0-9a-z-]+'),
    myRow('notification-controller', 'notification-controller-controller-manager-[0-9a-z-]+'),
    myRow('openshift-etcd', 'etcd-ip-[0-9]+-[0-9]+-[0-9]+-[0-9]+.ec2.internal'),
    myRow('openshift-kube-apiserver', 'kube-apiserver-ip-.+'),
    myRow('openshift-kueue-operator', 'kueue-controller-manager-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'pipeline-metrics-exporter-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'pipelines-as-code-controller-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'pipelines-as-code-watcher-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'pipelines-as-code-webhook-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'pipelines-console-plugin-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tekton-chains-controller-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tekton-events-controller-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tekton-operator-proxy-webhook-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tekton-pipelines-controller-[0-9]+'),
    myRow('openshift-pipelines', 'tekton-pipelines-remote-resolvers-[0-9]+'),
    myRow('openshift-pipelines', 'tekton-triggers-controller-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tekton-triggers-core-interceptors-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tekton-triggers-webhook-[0-9a-z-]+'),
    myRow('openshift-pipelines', 'tkn-cli-serve-[0-9a-z-]+'),
    myRow('product-kubearchive', 'apiserversource-kubearchive-[0-9a-z-]+'),
    myRow('product-kubearchive', 'kubearchive-api-server-[0-9a-z-]+'),
    myRow('product-kubearchive', 'kubearchive-sink-[0-9a-z-]+'),
    myRow('product-kubearchive', 'otel-collector-[0-9a-z-]+'),
    myRow('product-kubearchive', 'postgresql-[0-9a-z-]+'),
    myRow('project-controller', 'project-controller-controller-manager-[0-9a-z-]+'),
    myRow('release-service', 'release-service-controller-manager-[0-9a-z-]+'),
    myRow('release-service', 'release-service-monitor-deployment-[0-9a-z-]+'),
    myRow('repository-validator', 'repository-validator-controller-manager-[0-9a-z-]+'),
    myRow('tekton-kueue', 'tekton-kueue-controller-manager-[0-9a-z-]+'),
    myRow('tekton-kueue', 'tekton-kueue-webhook-[0-9a-z-]+'),
    myRow('tekton-logging', 'vector-tekton-logs-collector-.+'),
    myRow('tekton-results', 'tekton-results-api-.+'),
    myRow('tekton-results', 'tekton-results-api-for-watcher-.+'),
    myRow('tekton-results', 'tekton-results-watcher-.+'),
  ], panelWidth=8)
)

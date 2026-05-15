local grafonnet = import 'github.com/grafana/grafonnet/gen/grafonnet-latest/main.libsonnet';


// Just some shortcuts
local dashboard = grafonnet.dashboard;
local timeSeries = grafonnet.panel.timeSeries;

{
  // Define "datasource" variable
  datasourceVar::
    grafonnet.dashboard.variable.datasource.new(
      'datasource',
      'prometheus',
    )
    + grafonnet.dashboard.variable.datasource.withRegex('.*-(appstudio)-.*')
    + grafonnet.dashboard.variable.custom.generalOptions.withLabel('datasource')
    + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
      'Description'
    )
    + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('prometheus-appstudio-ds'),


  // Define CPU usage related queries we will use
  cpuQueryCurrent(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="%s",pod=~"%s"}[$__rate_interval]))' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withInterval('15s')
    + grafonnet.query.prometheus.withLegendFormat('{{pod}}'),
  cpuQueryRequests(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'sum by (pod) (avg(kube_pod_container_resource_requests{resource="cpu", namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('REQUESTS'),
  cpuQueryLimits(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'sum by (pod) (avg(kube_pod_container_resource_limits{resource="cpu", namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('LIMITS'),
  cpuQueryFloatingAvg(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'avg_over_time(sum by (pod) (avg(rate(container_cpu_usage_seconds_total{namespace="%s",pod=~"%s"}[$__rate_interval])))[24h:])' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withInterval('15s')
    + grafonnet.query.prometheus.withLegendFormat('24h AVG'),

  // Define memory usage related queries we will use
  memQueryCurrent(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'sum by (pod) (container_memory_working_set_bytes{namespace="%s",pod=~"%s",container=""})' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('{{pod}}'),
  memQueryRequests(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'avg(sum by (pod) (kube_pod_container_resource_requests{resource="memory",namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('REQUESTS'),
  memQueryLimits(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'avg(sum by (pod) (kube_pod_container_resource_limits{resource="memory",namespace="%s",pod=~"%s"}))' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('LIMITS'),
  memQueryFloatingAvg(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'avg_over_time(avg(sum by (pod) (container_memory_working_set_bytes{namespace="%s",pod=~"%s",container=""}))[24h:])' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('24h AVG'),

  // Define restarts count related query we will use
  restartsQueryCurrent(namespace, pod_rex)::
    grafonnet.query.prometheus.new(
      '$' + self.datasourceVar.name,
      'sum(kube_pod_container_status_restarts_total{namespace="%s",pod=~"%s"})' % [namespace, pod_rex],
    )
    + grafonnet.query.prometheus.withLegendFormat('restarts'),


  // Define panel functions
  myCPUPanel(namespace, pod_rex)::
    timeSeries.new('CPU usage for %s / %s' % [namespace, pod_rex])
    + timeSeries.queryOptions.withDatasource(
      type='prometheus',
      uid='$datasource',
    )
    + timeSeries.queryOptions.withTargets([
      self.cpuQueryCurrent(namespace, pod_rex),
      self.cpuQueryRequests(namespace, pod_rex),
      self.cpuQueryLimits(namespace, pod_rex),
      self.cpuQueryFloatingAvg(namespace, pod_rex),
    ])
    + timeSeries.standardOptions.withUnit('short')
    + timeSeries.standardOptions.withMin(0)
    + timeSeries.gridPos.withW(8)
    + timeSeries.gridPos.withH(8),

  myMemoryPanel(namespace, pod_rex)::
    timeSeries.new('Mem usage for %s / %s' % [namespace, pod_rex])
    + timeSeries.queryOptions.withDatasource(
      type='prometheus',
      uid='$datasource',
    )
    + timeSeries.queryOptions.withTargets([
      self.memQueryCurrent(namespace, pod_rex),
      self.memQueryRequests(namespace, pod_rex),
      self.memQueryLimits(namespace, pod_rex),
      self.memQueryFloatingAvg(namespace, pod_rex),
    ])
    + timeSeries.standardOptions.withUnit('bytes')
    + timeSeries.standardOptions.withMin(0)
    + timeSeries.gridPos.withW(8)
    + timeSeries.gridPos.withH(8),

  myRestartsPanel(namespace, pod_rex)::
    timeSeries.new('Restarts of %s / %s' % [namespace, pod_rex])
    + timeSeries.queryOptions.withDatasource(
      type='prometheus',
      uid='$datasource',
    )
    + timeSeries.queryOptions.withTargets([
      self.restartsQueryCurrent(namespace, pod_rex),
    ])
    + timeSeries.standardOptions.withUnit('short')
    + timeSeries.gridPos.withW(8)
    + timeSeries.gridPos.withH(8),

  myRow(namespace, pod_rex)::
    grafonnet.panel.row.new('Info about %s / %s' % [namespace, pod_rex])
    + grafonnet.panel.row.withPanels([
      self.myCPUPanel(namespace, pod_rex),
      self.myMemoryPanel(namespace, pod_rex),
      self.myRestartsPanel(namespace, pod_rex),
    ]),

  overviewDashboard(title, panels)::
    dashboard.new(title)
    + dashboard.withDescription('Dashboard visualizes Konflux controllers basic resources use and health indicators: CPU, memory usage and number of restarts. Aim is to help spot unusual behavior.')
    + dashboard.time.withFrom(value='now-24h')
    + dashboard.withVariables([
      self.datasourceVar,
    ])
    + dashboard.withPanels(
      grafonnet.util.grid.makeGrid(panels, panelWidth=8)
    ),
}

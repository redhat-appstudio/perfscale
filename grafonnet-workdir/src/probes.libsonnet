local grafonnet = import 'github.com/grafana/grafonnet/gen/grafonnet-latest/main.libsonnet';

// Just some shortcuts
local dashboard = grafonnet.dashboard;
local timeSeries = grafonnet.panel.timeSeries;
local stat = grafonnet.panel.stat;
local table = grafonnet.panel.table;
local row = grafonnet.panel.row;
local pieChart = grafonnet.panel.pieChart;

{
  // Define "datasource" variable
  datasourceVar()::
    grafonnet.dashboard.variable.datasource.new(
      'datasource',
      'postgres',
    )
    + grafonnet.dashboard.variable.datasource.withRegex('.*grafana-postgresql-datasource.*')  // TODO
    + grafonnet.dashboard.variable.custom.generalOptions.withLabel('Datasource')
    + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
      'Description'
    )
    + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('grafana-postgresql-datasource'),


  // Define "member_cluster" multi-select variable
  memberClusterVar(values)::
    grafonnet.dashboard.variable.custom.new(
      'member_cluster',
      values=values,
    )
    + grafonnet.dashboard.variable.custom.generalOptions.withLabel('Member cluster')
    + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
      'Description'
    )
    + grafonnet.dashboard.variable.custom.selectionOptions.withMulti()
    + grafonnet.dashboard.variable.query.selectionOptions.withIncludeAll()
    + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('all'),


  // Define "smoothing" variable
  smoothingVar()::
    grafonnet.dashboard.variable.custom.new(
      'smoothing',
      values=[
        'Off',
        '3 hours',
        '12 hours',
        '1 day',
        '3 days',
      ],
    )
    + grafonnet.dashboard.variable.custom.generalOptions.withLabel('Smoothing')
    + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
      'Description'
    )
    + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('Off'),


  durationQuery(testId, repoType, fieldName, includePassingFilter=true):: {
    local passingFilter = if includePassingFilter then "AND label_values->>'.results.measurements.KPI.mean' != '-1'" else '',
    rawSql: |||
      SELECT
          EXTRACT(EPOCH FROM start) AS "time",
          CASE
              WHEN '${smoothing}' = '3 hours' THEN AVG((label_values->>'%s')::DOUBLE PRECISION) OVER (ORDER BY start RANGE '3 hours' PRECEDING)
              WHEN '${smoothing}' = '12 hours' THEN AVG((label_values->>'%s')::DOUBLE PRECISION) OVER (ORDER BY start RANGE '12 hours' PRECEDING)
              WHEN '${smoothing}' = '1 day' THEN AVG((label_values->>'%s')::DOUBLE PRECISION) OVER (ORDER BY start RANGE '1 day' PRECEDING)
              WHEN '${smoothing}' = '3 days' THEN AVG((label_values->>'%s')::DOUBLE PRECISION) OVER (ORDER BY start RANGE '3 days' PRECEDING)
              ELSE (label_values->>'%s')::DOUBLE PRECISION
          END AS "value",
          '%s' as "metric"
      FROM
          data
      WHERE
          horreum_testid = %g
          AND label_values->>'.metadata.env.MEMBER_CLUSTER' = ${member_cluster}
          AND label_values->>'.repo_type' = '%s'
          AND $__timeFilter(start)
          %s
      ORDER BY
          start;
    ||| % [fieldName, fieldName, fieldName, fieldName, fieldName, fieldName, testId, repoType, passingFilter],
    format: 'time_series',
  },


  durationsQuery(testId, repoType, fieldNames, includePassingFilter=true)::
    timeSeries.queryOptions.withTargets(
      [self.durationQuery(testId, repoType, fieldName, includePassingFilter) for fieldName in fieldNames],
    ),


  errorsTableQuery(testId, repoType):: {
    rawSql: |||
      SELECT
          EXTRACT(EPOCH FROM start) AS "time",
          label_values->>'__results_errors_error_reasons_simple' AS "Error reasons"
      FROM
          data
      WHERE
          horreum_testid = %g
          AND label_values->>'.metadata.env.MEMBER_CLUSTER' = ${member_cluster}
          AND label_values->>'.repo_type' = '%s'
          AND $__timeFilter(start)
      ORDER BY
          start DESC;
    ||| % [testId, repoType],
    format: 'time_series',
  },


  errorsPieQuery(testId, repoType):: {
    rawSql: |||
      SELECT
          COALESCE(
              CASE
                  WHEN label_values ? '__results_errors_error_reasons_simple' THEN
                      regexp_replace(label_values->>'__results_errors_error_reasons_simple', '[0-9]+x ', '', 'g')
                  ELSE
                      NULL
              END,
              '') AS "Error",
          COUNT(*) AS "Count"
      FROM
          data
      WHERE
          horreum_testid = %g
          AND label_values->>'.metadata.env.MEMBER_CLUSTER' = ${member_cluster}
          AND label_values->>'.repo_type' = '%s'
          AND $__timeFilter(start)
      GROUP BY
          "Error"
      ORDER BY
          "Error" ASC;
    ||| % [testId, repoType],
    format: 'table',
  },


  durationsPanel(testId, repoType, fieldNames, fieldUnit, panelName='', includePassingFilter=true)::
    local title = if panelName == '' then std.join(',', fieldNames) else panelName;
    timeSeries.new('%s on ${member_cluster}' % title)
    + timeSeries.queryOptions.withDatasource(
      type='postgres',
      uid='${datasource}',
    )
    + timeSeries.fieldConfig.defaults.custom.withInsertNulls(5400000)
    + timeSeries.gridPos.withH(8)
    + timeSeries.gridPos.withW(24)
    + timeSeries.panelOptions.withMaxPerRow(4)
    + timeSeries.panelOptions.withRepeatDirection(value='h')
    + timeSeries.panelOptions.withRepeat('member_cluster')
    + timeSeries.queryOptions.withTransformations([])
    + timeSeries.standardOptions.withMin(0)
    + timeSeries.standardOptions.withUnit(fieldUnit)
    + self.durationsQuery(testId, repoType, fieldNames, includePassingFilter),


  errorsCountPanel(testId, repoType, fieldNames, panelName='')::
    local title = if panelName == '' then std.join(',', fieldNames) else panelName;
    stat.new('%s on ${member_cluster}' % title)
    + stat.queryOptions.withDatasource(
      type='postgres',
      uid='${datasource}',
    )
    + stat.gridPos.withH(8)
    + stat.gridPos.withW(24)
    + stat.options.reduceOptions.withCalcs(['mean'])
    + stat.options.reduceOptions.withValues(false)
    + stat.panelOptions.withMaxPerRow(4)
    + stat.panelOptions.withRepeatDirection(value='h')
    + stat.panelOptions.withRepeat('member_cluster')
    + stat.queryOptions.withTransformations([])
    + stat.standardOptions.color.withMode('thresholds')
    + stat.standardOptions.thresholds.withMode('absolute')
    + stat.standardOptions.thresholds.withSteps([{ color: 'green', value: null }, { color: 'red', value: 0.1 }])
    + stat.standardOptions.withMin(0)
    + stat.standardOptions.withUnit('percentunit')
    + self.durationsQuery(testId, repoType, fieldNames, false),


  errorsTablePanel(testId, repoType)::
    table.new('Error reasons detail on ${member_cluster}')
    + table.queryOptions.withDatasource(
      type='postgres',
      uid='${datasource}',
    )
    + table.fieldConfig.defaults.custom.withFilterable()
    + table.gridPos.withH(10)
    + table.gridPos.withW(24)
    + table.options.footer.withEnablePagination()
    + table.panelOptions.withMaxPerRow(4)
    + table.panelOptions.withRepeatDirection(value='h')
    + table.panelOptions.withRepeat('member_cluster')
    + table.queryOptions.withTransformations([])
    + table.standardOptions.withMin(0)
    + table.standardOptions.withUnit('string')
    + table.queryOptions.withTargets([self.errorsTableQuery(testId, repoType)]),


  errorsPiePanel(testId, repoType)::
    pieChart.new('Error reasons overall on ${member_cluster}')
    + pieChart.queryOptions.withDatasource(
      type='postgres',
      uid='${datasource}',
    )
    + pieChart.gridPos.withH(10)
    + pieChart.gridPos.withW(24)
    + pieChart.options.reduceOptions.withValues(true)
    + pieChart.options.withDisplayLabels(['value'])
    + pieChart.panelOptions.withMaxPerRow(4)
    + pieChart.panelOptions.withRepeatDirection(value='h')
    + pieChart.panelOptions.withRepeat('member_cluster')
    + pieChart.queryOptions.withTransformations([])
    + pieChart.standardOptions.withMin(0)
    + pieChart.standardOptions.withNoValue('no error detected')
    + pieChart.standardOptions.withUnit('none')
    + pieChart.queryOptions.withTargets([self.errorsPieQuery(testId, repoType)]),


  completeDashboard(
    dashboardName='',
    dashboardUid='',
    dashboardDescription='',
    testId=0,
    repoType='',
    memberClusters=[],
    testPhaseStubs=[],
    taskRunStubs=[],
  )::
    local datasourceVar = self.datasourceVar();
    local memberClusterVar = self.memberClusterVar(memberClusters);
    local smoothingVar = self.smoothingVar();
    dashboard.new(dashboardName)
    + dashboard.withUid(dashboardUid)
    + dashboard.withDescription(dashboardDescription)
    + dashboard.time.withFrom(value='now-7d')
    + dashboard.withVariables([
      datasourceVar,
      memberClusterVar,
      smoothingVar,
    ])
    + dashboard.withPanels([
      // Main panels
      row.new('KPI durations'),
      self.durationsPanel(testId, repoType, ['__results_measurements_KPI_mean'], 's', 'Mean duration'),
      row.new('KPI errors'),
      self.errorsCountPanel(testId, repoType, ['__results_measurements_KPI_errors'], 'Failure rate'),
      row.new('Errors table'),
      self.errorsTablePanel(testId, repoType),
      row.new('Errors pie-chart'),
      self.errorsPiePanel(testId, repoType),
      // Panels splitting test actions
      row.new('Duration by test phase'),
      self.durationsPanel(testId, repoType, [i + 'pass_duration_mean' for i in testPhaseStubs], 's', 'Duration by test phase'),
      row.new('Error rate by test phase'),
      self.durationsPanel(testId, repoType, [i + 'error_rate' for i in testPhaseStubs], 'none', 'Error rate by test phase', includePassingFilter=false),
      // Panels showing per task data
      row.new('Overall duration by task run'),
      self.durationsPanel(testId, repoType, [i + 'passed_duration_mean' for i in taskRunStubs], 's', 'Overall duration by task run'),
      row.new('Running duration by task run'),
      self.durationsPanel(testId, repoType, [i + 'passed_running_mean' for i in taskRunStubs], 's', 'Running duration by task run'),
      row.new('Scheduled duration by task run'),
      self.durationsPanel(testId, repoType, [i + 'passed_scheduled_mean' for i in taskRunStubs], 's', 'Scheduled duration by task run'),
      row.new('Idle duration by task run'),
      self.durationsPanel(testId, repoType, [i + 'passed_idle_mean' for i in taskRunStubs], 's', 'Idle duration by task run'),
      row.new('Count of task runs'),
      self.durationsPanel(testId, repoType, [i + 'passed_duration_samples' for i in taskRunStubs], 'none', 'Count of task runs'),
    ]),

}

local grafonnet = import 'github.com/grafana/grafonnet/gen/grafonnet-latest/main.libsonnet';

// Just some shortcuts
local dashboard = grafonnet.dashboard;
local timeSeries = grafonnet.panel.timeSeries;
local table = grafonnet.panel.table;

// Define "datasource" variable
local datasourceVar =
  grafonnet.dashboard.variable.datasource.new(
    'datasource',
    'grafana-postgresql-datasource',
  )
  + grafonnet.dashboard.variable.datasource.withRegex('.*grafana-postgresql-datasource.*')  // TODO
  + grafonnet.dashboard.variable.custom.generalOptions.withLabel('Datasource')
  + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
    'Description'
  )
  + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('grafana-postgresql-datasource');

// Define "member_cluster" multi-select variable
local memberClusterVar =
  grafonnet.dashboard.variable.custom.new(
    'member_cluster',
    values=[
      'https://api.stone-prod-p02.hjvn.p1.openshiftapps.com:6443/',
    ],
  )
  + grafonnet.dashboard.variable.custom.generalOptions.withLabel('Member cluster')
  + grafonnet.dashboard.variable.custom.generalOptions.withDescription(
    'Description'
  )
  + grafonnet.dashboard.variable.custom.selectionOptions.withMulti()
  + grafonnet.dashboard.variable.query.selectionOptions.withIncludeAll()
  + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('all');

local smoothingVar =
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
  + grafonnet.dashboard.variable.custom.generalOptions.withCurrent('Off');

// Panel query
local queryTarget(testId, fieldName, includePassingFilter=true) = {
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
        AND label_values->>'.metadata.env.MEMBER_CLUSTER' = '${member_cluster}'
        AND label_values->>'.repo_type' = 'libecpg-test-fork'
        %s
    ORDER BY
        start;
  ||| % [fieldName, fieldName, fieldName, fieldName, fieldName, fieldName, testId, passingFilter],
  format: 'time_series',
};
local queryTargets(testId, fieldNames, includePassingFilter=true) = timeSeries.queryOptions.withTargets(
  [queryTarget(testId, fieldName, includePassingFilter) for fieldName in fieldNames],
);

// Panel finally
local kpiPanel(testId, fieldNames, fieldUnit, panelName='', includePassingFilter=true) =
  local title = if panelName == '' then std.join(',', fieldNames) else panelName;
  timeSeries.new('%s on ${member_cluster}' % title)
  + timeSeries.queryOptions.withDatasource(
    type='grafana-postgresql-datasource',
    uid='${datasource}',
  )
  + timeSeries.standardOptions.withUnit(fieldUnit)
  + timeSeries.standardOptions.withMin(0)
  + timeSeries.panelOptions.withRepeat('member_cluster')
  + timeSeries.panelOptions.withRepeatDirection(value='h')
  + timeSeries.panelOptions.withMaxPerRow(6)
  + timeSeries.queryOptions.withTransformations([])
  + queryTargets(testId, fieldNames, includePassingFilter)
  + timeSeries.gridPos.withW(24)
  + timeSeries.gridPos.withH(8);

local errorPanel() =
  table.new('Error reasons on ${member_cluster}')
  + table.queryOptions.withDatasource(
    type='grafana-postgresql-datasource',
    uid='${datasource}',
  )
  + table.standardOptions.withUnit('string')
  + table.standardOptions.withMin(0)
  + table.panelOptions.withRepeat('member_cluster')
  + table.panelOptions.withRepeatDirection(value='h')
  + table.panelOptions.withMaxPerRow(6)
  + table.queryOptions.withTransformations([])
  + table.options.footer.withEnablePagination()
  + table.fieldConfig.defaults.custom.withFilterable()
  + table.queryOptions.withTargets([
    {
      rawSql: |||
        SELECT
            EXTRACT(EPOCH FROM start) AS "time",
            label_values->>'__results_errors_error_reasons_simple' AS "Error reasons"
        FROM
            data
        WHERE
            horreum_testid = 372
            AND label_values->>'.metadata.env.MEMBER_CLUSTER' = '${member_cluster}'
            AND label_values->>'.repo_type' = 'libecpg-test-fork'
            AND $__timeFilter(start)
        ORDER BY
            start DESC;
      |||,
      format: 'time_series',
    },
  ])
  + table.gridPos.withW(24)
  + table.gridPos.withH(10);

dashboard.new('Konflux clusters load-test RPM probe results')
+ dashboard.withDescription('Dashboard visualizes Konflux clusters load-test RPM probe results. Related Horreum test is https://horreum.corp.redhat.com/test/372 with filter by label `.repo_type = libecpg-test-fork`.')
+ dashboard.time.withFrom(value='now-24h')
+ dashboard.withVariables([
  datasourceVar,
  memberClusterVar,
  smoothingVar,
])
+ dashboard.withPanels([
  // Main panels
  kpiPanel(372, ['__results_measurements_KPI_mean'], 's', 'Mean duration'),
  kpiPanel(372, ['__results_measurements_KPI_errors'], 'none', 'Failures', includePassingFilter=false),
  errorPanel(),
  // Panels splitting test actions
  kpiPanel(372, [
    '__results_measurements_HandleUser_pass_duration_mean',
    '__results_measurements_createApplication_pass_duration_mean',
    '__results_measurements_createComponent_pass_duration_mean',
    '__results_measurements_createIntegrationTestScenario_pass_duration_mean',
    '__results_measurements_validateApplication_pass_duration_mean',
    '__results_measurements_validateIntegrationTestScenario_pass_duration_mean',
    '__results_measurements_validatePipelineRunCondition_pass_duration_mean',
    '__results_measurements_validatePipelineRunCreation_pass_duration_mean',
    '__results_measurements_validatePipelineRunSignature_pass_duration_mean',
    '__results_measurements_validateSnapshotCreation_pass_duration_mean',
    '__results_measurements_validateTestPipelineRunCondition_pass_duration_mean',
    '__results_measurements_validateTestPipelineRunCreation_pass_duration_mean',
  ], 's', 'Duration by test phase'),
  kpiPanel(372, [
    '__results_measurements_HandleUser_error_rate',
    '__results_measurements_createApplication_error_rate',
    '__results_measurements_createComponent_error_rate',
    '__results_measurements_createIntegrationTestScenario_error_rate',
    '__results_measurements_validateApplication_error_rate',
    '__results_measurements_validateIntegrationTestScenario_error_rate',
    '__results_measurements_validatePipelineRunCondition_error_rate',
    '__results_measurements_validatePipelineRunCreation_error_rate',
    '__results_measurements_validatePipelineRunSignature_error_rate',
    '__results_measurements_validateSnapshotCreation_error_rate',
    '__results_measurements_validateTestPipelineRunCondition_error_rate',
    '__results_measurements_validateTestPipelineRunCreation_error_rate',
  ], 'none', 'Error rate by test phase', includePassingFilter=false),
])

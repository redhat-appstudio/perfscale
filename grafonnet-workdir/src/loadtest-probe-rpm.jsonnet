local grafonnet = import 'github.com/grafana/grafonnet/gen/grafonnet-latest/main.libsonnet';

// Just some shortcuts
local dashboard = grafonnet.dashboard;
local timeSeries = grafonnet.panel.timeSeries;
local stat = grafonnet.panel.stat;
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
        AND $__timeFilter(start)
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
  + timeSeries.fieldConfig.defaults.custom.withInsertNulls(5400000)
  + queryTargets(testId, fieldNames, includePassingFilter)
  + timeSeries.gridPos.withW(24)
  + timeSeries.gridPos.withH(8);

local kpiErrorsPanel(testId, fieldNames, panelName='') =
  local title = if panelName == '' then std.join(',', fieldNames) else panelName;
  stat.new('%s on ${member_cluster}' % title)
  + stat.queryOptions.withDatasource(
    type='grafana-postgresql-datasource',
    uid='${datasource}',
  )
  + stat.standardOptions.withUnit('percentunit')
  + stat.standardOptions.withMin(0)
  + stat.standardOptions.color.withMode('thresholds')
  + stat.standardOptions.thresholds.withMode('absolute')
  + stat.standardOptions.thresholds.withSteps([{ color: 'green', value: null }, { color: 'red', value: 0.1 }])
  + stat.panelOptions.withRepeat('member_cluster')
  + stat.panelOptions.withRepeatDirection(value='h')
  + stat.panelOptions.withMaxPerRow(6)
  + stat.queryOptions.withTransformations([])
  + stat.options.reduceOptions.withCalcs(['mean'])
  + stat.options.reduceOptions.withValues(false)
  + queryTargets(testId, fieldNames, false)
  + stat.gridPos.withW(24)
  + stat.gridPos.withH(8);

local errorTablePanel() =
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

dashboard.new('Konflux clusters loadtest RPM probe results')
+ dashboard.withUid('Konflux_clusters_loadtest_RPM_probe_res')
+ dashboard.withDescription('Dashboard visualizes Konflux clusters loadtest RPM probe results. Related Horreum test is https://horreum.corp.redhat.com/test/372 with filter by label `.repo_type = libecpg-test-fork`.')
+ dashboard.time.withFrom(value='now-7d')
+ dashboard.withVariables([
  datasourceVar,
  memberClusterVar,
  smoothingVar,
])
+ dashboard.withPanels([
  // Main panels
  kpiPanel(372, ['__results_measurements_KPI_mean'], 's', 'Mean duration'),
  kpiErrorsPanel(372, ['__results_measurements_KPI_errors'], 'Failure rate'),
  errorTablePanel(),
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
  // Panels showing per task data
  kpiPanel(372, [
    '__results_durations_stats_taskruns__build_calculate_deps__passed_duration_mean',
    '__results_durations_stats_taskruns__build_check_noarch__passed_duration_mean',
    '__results_durations_stats_taskruns__build_get_rpm_sources__passed_duration_mean',
    '__results_durations_stats_taskruns__build_git_clone_oci_ta__passed_duration_mean',
    '__results_durations_stats_taskruns__build_import_to_quay__passed_duration_mean',
    '__results_durations_stats_taskruns__build_init__passed_duration_mean',
    '__results_durations_stats_taskruns__build_rpmbuild__passed_duration_mean',
    '__results_durations_stats_taskruns__build_show_sbom__passed_duration_mean',
    '__results_durations_stats_taskruns__build_summary__passed_duration_mean',
  ], 's', 'Overall duration by task run'),
  kpiPanel(372, [
    '__results_durations_stats_taskruns__build_calculate_deps__passed_running_mean',
    '__results_durations_stats_taskruns__build_check_noarch__passed_running_mean',
    '__results_durations_stats_taskruns__build_get_rpm_sources__passed_running_mean',
    '__results_durations_stats_taskruns__build_git_clone_oci_ta__passed_running_mean',
    '__results_durations_stats_taskruns__build_import_to_quay__passed_running_mean',
    '__results_durations_stats_taskruns__build_init__passed_running_mean',
    '__results_durations_stats_taskruns__build_rpmbuild__passed_running_mean',
    '__results_durations_stats_taskruns__build_show_sbom__passed_running_mean',
    '__results_durations_stats_taskruns__build_summary__passed_running_mean',
  ], 's', 'Running duration by task run'),
  kpiPanel(372, [
    '__results_durations_stats_taskruns__build_calculate_deps__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_check_noarch__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_get_rpm_sources__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_git_clone_oci_ta__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_import_to_quay__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_init__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_rpmbuild__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_show_sbom__passed_scheduled_mean',
    '__results_durations_stats_taskruns__build_summary__passed_scheduled_mean',
  ], 's', 'Scheduled duration by task run'),
  kpiPanel(372, [
    '__results_durations_stats_taskruns__build_calculate_deps__passed_idle_mean',
    '__results_durations_stats_taskruns__build_check_noarch__passed_idle_mean',
    '__results_durations_stats_taskruns__build_get_rpm_sources__passed_idle_mean',
    '__results_durations_stats_taskruns__build_git_clone_oci_ta__passed_idle_mean',
    '__results_durations_stats_taskruns__build_import_to_quay__passed_idle_mean',
    '__results_durations_stats_taskruns__build_init__passed_idle_mean',
    '__results_durations_stats_taskruns__build_rpmbuild__passed_idle_mean',
    '__results_durations_stats_taskruns__build_show_sbom__passed_idle_mean',
    '__results_durations_stats_taskruns__build_summary__passed_idle_mean',
  ], 's', 'Idle duration by task run'),
  kpiPanel(372, [
    '__results_durations_stats_taskruns__build_calculate_deps__passed_duration_samples',
    '__results_durations_stats_taskruns__build_check_noarch__passed_duration_samples',
    '__results_durations_stats_taskruns__build_get_rpm_sources__passed_duration_samples',
    '__results_durations_stats_taskruns__build_git_clone_oci_ta__passed_duration_samples',
    '__results_durations_stats_taskruns__build_import_to_quay__passed_duration_samples',
    '__results_durations_stats_taskruns__build_init__passed_duration_samples',
    '__results_durations_stats_taskruns__build_rpmbuild__passed_duration_samples',
    '__results_durations_stats_taskruns__build_show_sbom__passed_duration_samples',
    '__results_durations_stats_taskruns__build_summary__passed_duration_samples',
  ], 'none', 'Count of task runs'),
  // Panels showing per task architecture data
  kpiPanel(372, [
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_amd64__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_amd64__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_arm64__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_arm64__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_s390x__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_s390x__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_ppc64le__passed_duration_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_ppc64le__passed_duration_mean',
  ], 's', 'Overall duration by platform task run'),
  kpiPanel(372, [
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_amd64__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_amd64__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_arm64__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_arm64__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_s390x__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_s390x__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_ppc64le__passed_running_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_ppc64le__passed_running_mean',
  ], 's', 'Running duration by platform task run'),
  kpiPanel(372, [
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_amd64__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_amd64__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_arm64__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_arm64__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_s390x__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_s390x__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_ppc64le__passed_scheduled_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_ppc64le__passed_scheduled_mean',
  ], 's', 'Scheduled duration by platform task run'),
  kpiPanel(372, [
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_amd64__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_amd64__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_arm64__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_arm64__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_s390x__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_s390x__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_ppc64le__passed_idle_mean',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_ppc64le__passed_idle_mean',
  ], 's', 'Idle duration by platform task run'),
  kpiPanel(372, [
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_amd64__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_amd64__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_arm64__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_arm64__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_s390x__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_s390x__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_calculate_deps_linux_ppc64le__passed_duration_samples',
    '__results_durations_stats_platformtaskruns__build_rpmbuild_linux_ppc64le__passed_duration_samples',
  ], 'none', 'Count of platform task runs'),
])

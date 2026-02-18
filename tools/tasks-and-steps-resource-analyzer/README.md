Scripts for extracting Memory and CPU Usage Metrics
====================================================

This directory contains scripts for extracting memory and CPU usage metrics (Max, P95, P90, Median) for each `task` and `step` executed inside Konflux clusters. These scripts were created while addressing: https://issues.redhat.com/browse/KONFLUX-6712.

The workflow supports:
- **Multi-cluster execution** - Iterates over all configured clusters
- **Memory and CPU metrics** - Max, P95, P90, and Median per task/step
- **Optimized batching** - Handles unlimited pods with intelligent batching; 1–30+ days with adaptive query resolution
- **Task-scoped queries** - Metrics only from pods belonging to the specified task
- **CSV / JSON / colorized text** - Multiple output formats; per-pod attribution (max memory / max CPU pod)
- **Metadata** - Namespace, component, application where available
- **Parallel clusters** - `--pll-clusters N` for concurrent cluster processing
- **Cluster connectivity validation** - Pre-flight check before data collection

Architecture Overview (ASCII Diagram)
===================================
```text
                                     ┌───────────────────────────────────────────┐
                                     │ wrapper_for_promql_for_all_clusters.sh    │
                                     │───────────────────────────────────────────│
                                     │ • Entry point for the entire workflow     │
                                     │ • Iterates over all Konflux clusters      │
                                     │ • Switches kube context per cluster       │
                                     └──────────────────────────────┬────────────┘
                                                                    │
                                                                    ▼
                   ┌────────────────────────────────────────────────────────────────────┐
                   │     kube context: (cluster-1 / cluster-2 / cluster-N)              │
                   └──────────────────────────────────────┬─────────────────────────────┘
                                                          │
                                                          ▼
          ┌──────────────────────────────────────────────────────────────────────────┐
          │                 wrapper_for_promql.sh (per cluster driver)               │
          │──────────────────────────────────────────────────────────────────────────│
          │ • Reads task list (task_name + step_name)                                │
          │ • For each <task, step> pair:                                            │
          │       - Calls Python script to list pods (filtered by task label)        │
          │       - Processes pods in batches (50 pods per batch) to avoid limits    │
          │       - Queries memory and CPU metrics for each batch                    │
          │       - Aggregates results across all batches                            │
          │ • Passes through OUTPUT_MODE (human, json, csv)                          │
          └──────────────────────────────┬───────────────────────────────────────────┘
                                         │
                                         ▼
                 ┌────────────────────────────────────────────────────────────┐
                 │   list_pods_for_a_particular_task.py                       │
                 │────────────────────────────────────────────────────────────│
                 │ • Accepts task, step, fuzzy namespace ("*-tenant")         │
                 │ • Discovers matching namespaces automatically              │
                 │ • Locates all pods belonging to that task/step             │
                 │ • Returns pod list to wrapper_for_promql.sh                │
                 └──────────────────────────────┬─────────────────────────────┘
                                                │
                                                ▼
             ┌─────────────────────────────────────────────────────────────────────┐
             │ query_prometheus_range.py (optimized)                               │
             │─────────────────────────────────────────────────────────────────────│
             │ • Accepts PromQL query, start time, end time                        │
             │ • Uses adaptive step sizing based on time range:                    │
             │        - ≤1 day: 30s step                                           │
             │        - ≤7 days: 5m step                                           │
             │        - ≤30 days: 15m step                                         │
             │        - >30 days: 1h step                                          │
             │ • Sends HTTP requests to Prometheus API                             │
             │ • Executes queries for:                                             │
             │        - container_memory_working_set_bytes (actual memory usage)   │
             │        - container_cpu_usage_seconds_total (with rate calculation)  │
             │        - container_cpu_usage_seconds_total (with rate calculation)  │
             │ • Returns time series data for aggregation                          │
             └──────────────────────────────┬──────────────────────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────────────────┐
                     │             Aggregators in wrapper_for_promql.sh         │
                     │──────────────────────────────────────────────────────────│
                     │ • Processes pods in batches (50 per batch)               │
                     │ • For each batch:                                        │
                     │        - Queries memory max, p95, p90, median            │
                     │          (uses container_memory_working_set_bytes for    │
                     │           actual usage, not limits)                      │
                     │        - Queries CPU max, p95, p90, median               │
                     │        - Validates returned pods belong to task          │
                     │ • Aggregates across all batches:                         │
                     │        - Finds global max memory pod and value           │
                     │        - Finds global max CPU pod and value              │
                     │        - Calculates accurate percentiles across all pods │
                     │ • Creates consolidated records                           │
                     │ • Delegates final formatting to output backends          │
                     └──────────────────────────────┬───────────────────────────┘
                                                    │
                                                    ▼
       ┌────────────────────────────────────────────────────────────────────────────┐
       │             Output Formatter (human / color / CSV / JSON)                  │
       │────────────────────────────────────────────────────────────────────────────│
       │ • Human mode:                                                              │
       │       - ANSI-colored tables                                                │
       │       - MAX above threshold marked in red                                  │
       │ • CSV mode:                                                                │
       │       - Comma-separated values for spreadsheets                            │
       │ • JSON mode:                                                               │
       │       - Machine-readable structured output                                 │
       │ • Output returned to wrapper_for_promql_for_all_clusters.sh                │
       └────────────────────────────────────────────────────────────────────────────┘
```


How to Run
===================================

Run for last N days:

    ./wrapper_for_promql_for_all_clusters.sh <num_of_days> [--csv] [--table] [--raw] [--debug]

**Examples:**

```bash
# Default: CSV output (pipeable, suitable for scripts)
./wrapper_for_promql_for_all_clusters.sh 7 --csv

# Table format: Human-readable table (automatically used when outputting to terminal)
./wrapper_for_promql_for_all_clusters.sh 7 --table

# Raw CSV: Explicitly request raw CSV output (for piping to other tools)
./wrapper_for_promql_for_all_clusters.sh 7 --raw

# Debug mode: Shows detailed query information
./wrapper_for_promql_for_all_clusters.sh 7 --csv --debug

# Pipe to analysis tool (uses raw CSV automatically)
./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py
```

**Output Format Behavior:**
- **Default (no flags)**: If output is to a terminal, shows table format. If piped, outputs raw CSV.
- **`--table`**: Explicitly request table format (useful for scripts)
- **`--raw`**: Explicitly request raw CSV (useful when you want CSV even in terminal)
- **`--csv`**: Legacy flag, same as default behavior

The PromQL range window and sampling delta adjust automatically based on the time range:
- **1 day**: 30-second resolution for fine-grained analysis
- **7 days**: 5-minute resolution (optimized for Prometheus limits)
- **30 days**: 15-minute resolution
- **30+ days**: 1-hour resolution

**Performance Optimizations:**
- **Intelligent Batching**: Pods are processed in batches of 50 to avoid URL length and query complexity limits
- **Task-Scoped Queries**: All metrics are validated to ensure they only come from pods belonging to the specified task
- **Adaptive Step Sizing**: Query resolution automatically adjusts to stay within Prometheus data point limits
- **Efficient Aggregation**: Percentiles are calculated accurately across all pods, not per-batch

Python Virtual Environment
===================================

It is recommended to create a venv:

    python -m venv promql_for_mem_metrics; source promql_for_mem_metrics/bin/activate

**Required Python packages:**
```bash
pip install requests pyyaml
```

CSV Output Format
===================================
The CSV output includes both memory and CPU metrics with separate pod attribution:

```
"cluster", "task", "step", "pod_max_mem", "namespace_max_mem", "component_max_mem", "application_max_mem", "mem_max_mb", "mem_p95_mb", "mem_p90_mb", "mem_median_mb", "pod_max_cpu", "namespace_max_cpu", "component_max_cpu", "application_max_cpu", "cpu_max", "cpu_p95", "cpu_p90", "cpu_median"
```

**Column Descriptions:**
- `pod_max_mem` - Pod name with the highest memory usage
- `namespace_max_mem` - Namespace of the pod with max memory
- `component_max_mem` - Component name for the max memory pod (if available)
- `application_max_mem` - Application name for the max memory pod (if available)
- `mem_max_mb` - Maximum memory usage in MB
- `mem_p95_mb`, `mem_p90_mb`, `mem_median_mb` - Memory percentiles in MB
- `pod_max_cpu` - Pod name with the highest CPU usage
- `namespace_max_cpu` - Namespace of the pod with max CPU
- `component_max_cpu` - Component name for the max CPU pod (if available)
- `application_max_cpu` - Application name for the max CPU pod (if available)
- `cpu_max`, `cpu_p95`, `cpu_p90`, `cpu_median` - CPU metrics in millicores (e.g., "3569m")

CSV Output Example
===================================
(Step values may appear with or without `step-` prefix. Data rows illustrate the shape; full CSV has 19 columns as in the header.)
```
"cluster","task","step","pod_max_mem","namespace_max_mem","component_max_mem","application_max_mem","mem_max_mb","mem_p95_mb","mem_p90_mb","mem_median_mb","pod_max_cpu","namespace_max_cpu","component_max_cpu","application_max_cpu","cpu_max","cpu_p95","cpu_p90","cpu_median"
"stone-prd-rh01","buildah","step-build","maestro-on-pull-request-wtpkk-build-container-pod","maestro-rhtap-tenant","N/A","N/A","8192","8191","8190","8183","operator-on-pull-request-45m69-build-container-pod","vp-operator-release-tenant","N/A","N/A","3569m","3569m","3569m","3569m"
"kflux-prd-rh02","buildah","step-push","175","rhobs-observato8863c7ac646c45ff10fb9046c502710ae37ef8bf870e-pod","rhobs-mco-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh02","buildah","step-sbom-syft-generate","10","rhobs-observatoee6e952803459989d848471898dd7a5ad76331c37d9f-pod","rhobs-mco-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh02","buildah","step-prepare-sboms","10","crc-binary-on-pull-request-6kgk9-build-container-pod","crc-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh02","buildah","step-upload-sbom","10","rhobs-observatorium-api-main-on-push-jvj2w-build-container-pod","rhobs-mco-tenant","N/A","N/A","5","5","5"
"kflux-prd-rh03","buildah","step-build","2218","rosa-log-router-processor-go-on-push-rhfrf-build-container-pod","rosa-log-router-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh03","buildah","step-push","291","rosa-log-router-api-on-push-7m4q6-build-container-pod","rosa-log-router-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh03","buildah","step-sbom-syft-generate","26","rosa-log-router-authorizer-on-push-9rjjt-build-container-pod","rosa-log-router-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh03","buildah","step-prepare-sboms","10","rosa-clusters-service-main-on-push-889lb-build-container-pod","ocm-tenant","N/A","N/A","0","0","0"
"kflux-prd-rh03","buildah","step-upload-sbom","10","rosa-log-router8a62df9c0d552405a950b37393ec02899f7d63a55582-pod","rosa-log-router-tenant","N/A","N/A","0","0","0"
"stone-prd-rh01","buildah","step-build","8192","maestro-on-pull-request-bd8gq-build-container-pod","maestro-rhtap-tenant","N/A","N/A","33","33","32"
"stone-prd-rh01","buildah","step-push","4096","mintmaker-renovate-image-onfb4dbfe538bfbe5501da4d85e1a8841b-pod","konflux-mintmaker-tenant","N/A","N/A","5","5","5"
"stone-prd-rh01","buildah","step-sbom-syft-generate","4096","mintmaker-renovate-image-on-push-6p2v2-build-container-pod","konflux-mintmaker-tenant","N/A","N/A","5","5","4"
"stone-prd-rh01","buildah","step-prepare-sboms","147","git-partition-sb74fb1d75007f3983ea4fcfb634aedfed55841810a57-pod","app-sre-tenant","N/A","N/A","5","5","5"
"stone-prd-rh01","buildah","step-upload-sbom","30","image-builder-frontend-on-p3a8a424d61bda08ebddf65ed7412c350-pod","insights-management-tenant","N/A","N/A","5","5","5"
"stone-prod-p02","buildah","step-build","8192","kubectl-package-internal-on-push-rzvcb-build-container-pod","mos-lpsre-tenant","N/A","N/A","32","32","19"
"stone-prod-p02","buildah","step-push","2029","ocmci-on-pull-request-zlcvq-build-container-pod","ocmci-tenant","N/A","N/A","0","0","0"
"stone-prod-p02","buildah","step-sbom-syft-generate","1375","osd-fleet-manager-main-on-p96be62f96f3b274d6a8261f92ea05f12-pod","fleet-manager-tenant","N/A","N/A","0","0","0"
"stone-prod-p02","buildah","step-prepare-sboms","12","ocm-ams-master-on-pull-request-hrmqm-build-container-pod","ocm-tenant","N/A","N/A","0","0","0"
"stone-prod-p02","buildah","step-upload-sbom","29","web-rca-ui-main-on-pull-request-jblvn-build-container-pod","hcm-eng-prod-tenant","N/A","N/A","0","0","0"
"stone-stg-rh01","buildah","step-build","0","","N/A","N/A","N/A","0","0","0"
"stone-stg-rh01","buildah","step-push","0","","N/A","N/A","N/A","0","0","0"
"stone-stg-rh01","buildah","step-sbom-syft-generate","0","","N/A","N/A","N/A","0","0","0"
"stone-stg-rh01","buildah","step-prepare-sboms","0","","N/A","N/A","N/A","0","0","0"
"stone-stg-rh01","buildah","step-upload-sbom","0","","N/A","N/A","N/A","0","0","0"
```
Resource Limit Analysis Tool
===================================

The `analyze_resource_limits.py` script analyzes resource consumption data and provides recommendations for Kubernetes resource limits with advanced features like caching, comparison tables, and patch file generation.

**Features:**

**Major Features:**
- **Parallel Cluster Processing**: Process multiple clusters concurrently with `--pll-clusters N` flag for faster data collection
- **Dry-Run Mode**: Validate task/steps and check cluster connectivity without running data collection using `--dry-run` flag
- **Unified Execution Modes**: Serial and parallel execution modes now have consistent progress indicators, summary output, and error reporting
- **Task/Step Validation**: Automatically validates wrapper-defined steps against YAML file steps before proceeding with analysis

**Core Features:**
- **Automatic Data Collection**: Can extract task/step info from YAML and automatically run data collection
- **Caching System**: Recommendations are cached, allowing review before applying changes
- **Comparison Tables**: Shows current vs proposed resource limits side-by-side
- **HTML Output for Trend Analysis**: Automatically generates HTML files with timestamped filenames:
  - **CSV Data HTML**: Sortable table of all collected resource usage data (`{task_name}_analyzed_data_{timestamp}.html`)
  - **Comparison HTML**: Non-sortable table showing current vs proposed resource limits (`{task_name}_comparison_data_{timestamp}.html`)
  - Files saved in `.analyze_cache/` directory with timestamps for trend analysis
  - HTML files are browser-compatible with no external dependencies
- **Configurable Base Metrics**: Choose calculation base (max, P95, P90, median) with configurable safety margin
- **Patch File Generation**: For remote GitHub URLs, generates `.patch` files for manual review
- **Smart Rounding**: 
  - Memory: Rounds UP to increments of 64Mi (< 1Gi) or whole Gi (>= 1Gi), minimum 64Mi
  - CPU: Rounds UP to increments of 50m, minimum 50m
- **Update from Cache**: Apply cached recommendations without re-running analysis

**User Experience Improvements:**
- **Progress Indicators**: Real-time progress spinner during data collection for both serial and parallel execution modes
- **Cluster Display Names**: Consolidated cluster name extraction shows short, user-friendly names (e.g., "stone-stg-rh01") instead of full context strings throughout the UI
- **Data Collection Summary**: Comprehensive summary report showing total clusters processed, successful/failed counts, and detailed error information
- **Cluster Connectivity Check**: Pre-flight validation of all clusters before proceeding with data collection, displayed in a clear connectivity report

**Usage Examples:**

## Basic Usage Patterns

**1. Analyze from piped CSV input:**
```bash
./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py
```

**2. Analyze from local YAML file (auto-runs data collection):**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml
```

**3. Analyze from GitHub URL (auto-runs data collection):**
```bash
./analyze_resource_limits.py --file https://github.com/konflux-ci/build-definitions/blob/main/task/buildah/0.7/buildah.yaml
```

## Phase 1: Analysis (Default Behavior)

**Important:** Phase 1 (analysis) generates recommendations for ALL base metrics (max, p95, p90, median) regardless of the `--base` flag. The `--base` flag is **ignored** in Phase 1.

**4. Basic analysis (generates all base metrics):**
```bash
# Generates recommendations for max, p95, p90, and median
# --base flag is ignored in Phase 1
./analyze_resource_limits.py --file /path/to/buildah.yaml
```

## Margin Configuration

**5. Custom safety margin (default is 5%):**
```bash
# Use default 5% margin
./analyze_resource_limits.py --file /path/to/buildah.yaml

# Add 10% safety margin
./analyze_resource_limits.py --file /path/to/buildah.yaml --margin 10

# Add 20% safety margin
./analyze_resource_limits.py --file /path/to/buildah.yaml --margin 20
```

**Note:** Margin is applied to all base metrics (max, p95, p90, median) in Phase 1.

## Time Range Configuration

**9. Custom data collection period (default is 7 days):**
```bash
# Analyze last 1 day
./analyze_resource_limits.py --file /path/to/buildah.yaml --days 1

# Analyze last 10 days
./analyze_resource_limits.py --file /path/to/buildah.yaml --days 10

# Analyze last 30 days
./analyze_resource_limits.py --file /path/to/buildah.yaml --days 30

# Combine with other options
./analyze_resource_limits.py --file /path/to/buildah.yaml --days 10 --base p95 --margin 5
```

## Parallel Processing

**10. Parallel cluster processing (faster for multiple clusters):**
```bash
# Process 3 clusters concurrently
./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 3

# Process 5 clusters concurrently
./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 5

# Combine with other options
./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 3 --days 10 --base p95 --margin 5
```

## Phase 2: Update (View Recommendations)

**Important:** Phase 2 (`--update`) requires `--file` to specify which task to update. The `--base` flag is **ignored** in Phase 2 - all base metrics (max, p95, p90, median) are always shown. YAML files are **NOT updated automatically** - you must update them manually after reviewing recommendations.

**11. View recommendations for all base metrics:**
```bash
# View recommendations for all base metrics (max, p95, p90, median)
# --base flag is ignored in Phase 2, margin must be specified
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 5

# Same as above (--base is ignored)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --base p95 --margin 5
```

**12. Update with different margin (creates new comparison files for each margin):**
```bash
# Create comparison files for margin 5% (if they don't exist)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 5

# Create comparison files for margin 10% (separate file, coexists with margin 5% file)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 10

# Create comparison files for margin 20% (separate file, coexists with other margin files)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 20

# If comparison file already exists for a margin, it's reused (no new file created)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 5
```

**13. Two-phase workflow (recommended):**
```bash
# Phase 1: Generate all base metrics (margin: 5%, days: 7)
./analyze_resource_limits.py --file /path/to/buildah.yaml --margin 5 --days 7

# Phase 2: View recommendations for all base metrics with margin 5%
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 5

# Phase 2: View recommendations for all base metrics with margin 10% (creates new file)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 10

# Phase 2: View recommendations for all base metrics with margin 20% (creates new file)
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --margin 20
```

**Note:** You can run Phase 2 multiple times with different `--margin` values. Each margin gets its own comparison file, allowing you to compare recommendations across different margins. The `--base` flag is ignored - all base metrics are always shown.

## Debug and Validation

**16. Enable debug output:**
```bash
# Show detailed processing information
./analyze_resource_limits.py --file /path/to/buildah.yaml --debug

# Debug with update
./analyze_resource_limits.py --update --file /path/to/buildah.yaml --debug

# Debug with all options
./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 3 --days 10 --base p95 --margin 5 --debug
```

**17. Dry-run: Validate task/steps and check cluster connectivity:**
```bash
# Check connectivity and validate configuration without running data collection
./analyze_resource_limits.py --file /path/to/buildah.yaml --dry-run

# Dry-run with debug for more details
./analyze_resource_limits.py --file /path/to/buildah.yaml --dry-run --debug
```

## Complete Examples with All Options

**18. Full-featured analysis:**
```bash
# Analyze with P95 base, 15% margin, 10 days data, 4 parallel workers, debug output
./analyze_resource_limits.py --file /path/to/buildah.yaml --base p95 --margin 15 --days 10 --pll-clusters 4 --debug
```

**19. Full-featured update workflow:**
```bash
# Step 1: Analyze with custom parameters
./analyze_resource_limits.py --file https://github.com/.../buildah.yaml --base p95 --margin 15 --days 10 --pll-clusters 4

# Step 2: Update local file using cache
./analyze_resource_limits.py --update --file /path/to/local/buildah.yaml --debug
```

**20. Piped input with analysis:**
```bash
# Collect data and analyze in one command
./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py
```

**21. Multiple independent task analyses (task-based caching):**
```bash
# Analyze task1 - saves to task1.json cache
./analyze_resource_limits.py --file /path/to/task1.yaml --margin 5 --days 10

# Analyze task2 - saves to task2.json cache (independent, doesn't overwrite task1 cache)
./analyze_resource_limits.py --file /path/to/task2.yaml --margin 10 --days 7

# Update task1 using its cache
./analyze_resource_limits.py --update --file /path/to/task1.yaml

# Update task2 using its cache
./analyze_resource_limits.py --update --file /path/to/task2.yaml
```

## Common Workflow Combinations

**Conservative approach (production-ready):**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --base max --margin 20 --days 14
```

**Balanced approach (recommended):**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --base p95 --margin 10 --days 7
```

**Aggressive optimization (cost-saving):**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --base p90 --margin 5 --days 7
```

**Quick validation:**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --dry-run
```

**Command-line Options:**

- **`--file FILE`** - YAML file path or GitHub URL to analyze
  - Can be a local file path or a GitHub URL
  - When provided, extracts task/step info and runs per-pod data collection (single source), then analysis
  - Required for initial analysis, optional when using `--update` with cache

- **`--update`** - Phase 2: View recommendations for all base metrics
  - **Requires `--file`**: Must specify which task to update
  - Extracts task name from the file and loads analysis data from Phase 1
  - Shows comparison tables for all base metrics (max, p95, p90, median) - `--base` flag is ignored
  - **Does NOT update YAML files**: You must update YAML files manually after reviewing recommendations
  - Creates comparison file for the specified margin if it doesn't exist: `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD}.html/json`
  - If comparison file already exists for the margin, uses existing file (no new file created)
  - Can be run multiple times with different `--margin` values, creating separate files for each margin
  - Multiple margin files coexist for the same analysis date, allowing easy comparison across margins
  - **No timestamp added** in Phase 2 (timestamp only added when re-analysis happens in Phase 1)

- **`--analyze-again`, `--aa`** - Force re-analysis even when cache exists
  - Only effective when used with `--update --file`
  - Ignores existing cache and runs fresh analysis before updating
  - Useful when you want to re-analyze with different parameters

- **`--margin MARGIN`** - Safety margin percentage to add to base metric (default: 5)
  - Formula: `recommended = base_metric * (1 + margin / 100)`
  - Common values: 5% (default), 10% (balanced), 20% (conservative)
  - Applied to all base metrics (max, p95, p90, median) in both Phase 1 and Phase 2
  - Final recommendation is capped at maximum observed value

- **`--base {max,p95,p90,median}`** - Base metric for margin calculation (default: max)
  - **Phase 1 (Analysis)**: This flag is **IGNORED**. All base metrics (max, p95, p90, median) are generated regardless.
  - **Phase 2 (Update)**: This flag is **IGNORED**. All base metrics are always shown regardless of this flag.
  - **`max`**: Maximum observed usage across all clusters (most conservative, default)
  - **`p95`**: 95th percentile usage (recommended for production, covers 95% of cases)
  - **`p90`**: 90th percentile usage (more aggressive, covers 90% of cases)
  - **`median`**: Median usage (most aggressive, covers 50% of cases)
  - Falls back to `max` if selected percentile data is unavailable
  - The tool finds the maximum of each metric across all clusters, then adds margin

- **`--days DAYS`** - Number of days for data collection when using `--file` (default: 7)
  - Range: Typically 1-30+ days
  - Longer periods provide more data but take longer to collect
  - Shorter periods are faster but may miss periodic spikes
  - Only used when `--file` is provided (not with piped input)

- **`--debug`** - Enable debug output showing detailed processing information
  - Shows step detection logic, YAML parsing details
  - Displays computeResources update operations
  - Useful for troubleshooting YAML update issues
  - Can be combined with any other option

- **`--pll-clusters N`** - Enable parallel processing across clusters with N workers
  - Only active during analysis phase (ignored during `--update`)
  - Processes N clusters concurrently for faster data collection
  - Each cluster uses a process-specific kubeconfig to avoid conflicts
  - Recommended for environments with many clusters (e.g., `--pll-clusters 3` or `--pll-clusters 5`)
  - Serial mode (default) processes clusters one by one

- **`--dry-run`** - Validate task/steps and check cluster connectivity without running data collection
  - Validates wrapper-defined steps against YAML file steps
  - Checks connectivity to all configured clusters
  - Displays cluster connectivity report
  - Exits without running data collection or analysis
  - Useful for troubleshooting configuration issues before full analysis

**How it works:**

**Phase 1: Analysis (default, unless `--update` is specified):**

1. **With `--file` (local or GitHub URL):**
   - Validates wrapper-defined steps against YAML file steps before proceeding
   - Checks cluster connectivity and displays connectivity report with short cluster names (e.g., "stone-stg-rh01")
   - Extracts task name, step names, and current resource limits from Tekton Task YAML
   - Collects per-pod metrics from all clusters (single source); derives max/p95/p90/median CSV for analysis
   - Shows data collection output in table format and analyzes data across all clusters for each step
   - **Generates recommendations for ALL base metrics** (max, p95, p90, median) - `--base` flag is ignored
   - Calculates recommendations using each base metric + safety margin
   - Rounds values to standard Kubernetes resource sizes
   - Saves analyzed data files (sortable HTML + JSON)
   - Saves detailed per-step files (one HTML/JSON/CSV per step): `{task_name}_analyzed_data_detailed_step_{step-name}_{YYYYMMDD}.html/json/csv` (step names without `step-` prefix, e.g. `prefetch-dependencies`)
   - Saves comparison data files with margin in filename:
     - First analysis: `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD}.html/json`
     - Re-analysis: `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD_HHMMSS}.html/json` (preserves old files)
   - Shows detailed analysis for default base (max)
   - Timestamp is only added when re-analysis happens (analyzed_data files exist for that date)

**Phase 2: Update (with `--update` flag):**

2. **With `--update --file`:**
   - Requires `--file` to specify which task to update
   - Loads analyzed data from Phase 1 (date-based files, handles both date-only and date+timestamp formats)
   - Checks if comparison file exists for the specified margin
   - If comparison file exists: Uses existing file (no new file created)
   - If comparison file doesn't exist: Creates new comparison file `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD}.html/json` (no timestamp, since no re-analysis)
   - Shows comparison tables for **all base metrics** (max, p95, p90, median) - `--base` flag is ignored
   - **Does NOT update YAML files** - you must update them manually
   - Can be run multiple times with different `--margin` values, creating separate files for each margin
   - Multiple margin files can coexist for the same analysis date, allowing easy comparison
   
3. **With piped input:**
   - Reads CSV data from stdin
   - Analyzes and provides recommendations for all base metrics
   - Does not save files (no file reference)

4. **With `--dry-run`:**
   - Validates wrapper-defined steps against YAML file steps
   - Checks connectivity to all configured clusters
   - Displays cluster connectivity report with short cluster names
   - Exits without running data collection or analysis
   - Useful for troubleshooting configuration issues before running full analysis

**Understanding Base Metrics:**

The tool calculates recommendations using a two-step process:

1. **Collect metrics per cluster**: For each step, the tool collects Max, P95, P90, and Median values from each cluster
2. **Find maximum across clusters**: For the selected base metric, it finds the maximum value across all clusters
3. **Add safety margin**: Applies the margin percentage to this maximum value
4. **Cap at observed maximum**: Final recommendation never exceeds the maximum observed value

**Example calculation with `--base p95 --margin 10`:**
- Cluster A: P95 memory = 2Gi
- Cluster B: P95 memory = 3Gi  
- Cluster C: P95 memory = 2.5Gi
- Maximum P95 across clusters = 3Gi
- Base for calculation = 3Gi
- Recommended = 3Gi × (1 + 10/100) = 3.3Gi
- If max observed memory was 3.5Gi, recommendation is capped at 3.5Gi
- Final recommendation (after rounding) = 4Gi

**Choosing the Right Base Metric:**

- **`max`** (default): Safest choice, ensures all observed usage patterns are covered. Use for production workloads where reliability is critical.
- **`p95`**: Recommended for most production workloads. Covers 95% of usage patterns, provides good balance between safety and resource efficiency.
- **`p90`**: More aggressive, suitable for cost optimization when occasional OOM kills are acceptable. Covers 90% of usage patterns.
- **`median`**: Most aggressive, only covers 50% of usage patterns. Use only for non-critical workloads or when cost is the primary concern.

**Example Output:**

**Cluster Connectivity Report:**
```
================================================================================
Checking Cluster Connectivity
================================================================================

Cluster Connectivity Report:
  ✓ stone-prd-rh01: Connected
  ✓ kflux-prd-rh02: Connected
  ✓ kflux-prd-rh03: Connected
  ✓ stone-prod-p02: Connected

✓ All clusters are accessible
```

**Data Collection Summary (shown during data collection):**
```
================================================================================
Data Collection Summary
================================================================================
Total clusters processed: 4
Successful: 4
Failed: 0
================================================================================

Collected data from 4 cluster(s), total CSV lines: 8
```

**Analysis Output:**
```
================================================================================
RESOURCE LIMIT RECOMMENDATIONS (Max + 10% Safety Margin)
================================================================================

Step: step-build
--------------------------------------------------------------------------------
  Memory: 8Gi
    - Base (Max): 8.0Gi
    - Coverage: 6/6 clusters

  CPU: 5100m
    - Base (Max): 4.67 cores
    - Coverage: 4/4 clusters

Step: step-push
--------------------------------------------------------------------------------
  Memory: 1Gi
    - Base (Max): 1024MB
    - Coverage: 5/6 clusters

  CPU: 400m
    - Base (Max): 0.385 cores
    - Coverage: 5/6 clusters
```

**Comparison Table Output:**
```
========================================================================================================================
RESOURCE LIMITS COMPARISON: CURRENT vs PROPOSED
========================================================================================================================

Step                      Current Requests               Proposed Requests              Current Limits                 Proposed Limits               
------------------------------------------------------------------------------------------------------------------------
build                     8Gi / 1                        8Gi / 5100m                    8Gi / null                     8Gi / 5100m                   
push                      4Gi / 1                        1Gi / 400m                     4Gi / null                     1Gi / 400m                    
sbom-syft-generate        4Gi / 1                        2Gi / 800m                     4Gi / null                     2Gi / 800m                    
prepare-sboms             512Mi / 100m                   192Mi / 50m                    512Mi / null                   192Mi / 50m                   
upload-sbom               512Mi / 100m                   64Mi / 50m                     512Mi / null                   64Mi / 50m                  
```

**Patch File Generation (for remote URLs):**
When using `--update` with a GitHub URL, a patch file is generated:
```
Generated patch file: buildah_20241219_141358.patch
File path in patch: task/buildah/0.7/buildah.yaml
Apply with: patch <original_file> < buildah_20241219_141358.patch
```

**Caching:**
- Cache files are stored in `.analyze_cache/` directory
- **Task-based caching**: Each task gets its own cache file based on task name (e.g., `clair-scan.json`, `buildah.json`)
- This allows multiple independent analyses and updates:
  - Run analysis on multiple tasks sequentially without overwriting caches
  - Each task's cache is independent and can be updated separately
  - Example: Analyze `task1.yaml` → saves to `task1.json`, then analyze `task2.yaml` → saves to `task2.json` (independent)
- Cache includes: task name, file path/URL, recommendations, margin, base metric, days, and timestamp
- When using `--update --file <local_file>`: Extracts task name from the file and loads cache for that specific task
- When using `--update` without `--file`: Shows all available cached tasks and uses the most recent one
- Use `--analyze-again` flag to force re-analysis even when cache exists for that task

**File Output (Phase 1 - Analysis):**
- **Date-based naming**: Files use date format (YYYYMMDD) for first analysis, or date+timestamp (YYYYMMDD_HHMMSS) if files already exist for that date
- **Analyzed Data Files**:
  - First analysis on a date: `{task_name}_analyzed_data_{YYYYMMDD}.html/json`
  - Re-running on same date: `{task_name}_analyzed_data_{YYYYMMDD_HHMMSS}.html/json` (preserves old files)
  - Sortable HTML table and JSON of aggregated CSV data
- **Detailed per-step files** (one HTML/JSON/CSV per step): `{task_name}_analyzed_data_detailed_step_{step-name}_{YYYYMMDD}.html/json/csv` — step names without `step-` prefix (e.g. `prefetch-dependencies`), sortable tables with current requests/limits in Kubernetes format
- **Comparison Data Files** (margin-specific, created in both Phase 1 and Phase 2):
  - **Phase 1 (first analysis)**: `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD}.html/json`
  - **Phase 1 (re-analysis)**: `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD_HHMMSS}.html/json` (preserves old files)
  - **Phase 2 (new margin)**: `{task_name}_comparison_data_margin-{margin}_{YYYYMMDD}.html/json` (no timestamp, since no re-analysis)
  - Comparison tables for all base metrics (max, p95, p90, median)
  - All recommendations for all base metrics
  - Each margin gets its own file, allowing multiple margin files to coexist for the same analysis
  - Files are only created if they don't already exist for that margin
- **Date Format**: `YYYYMMDD` (e.g., `20250113`) or `YYYYMMDD_HHMMSS` (e.g., `20250113_165921`) when re-analysis happens
- **File Location**: All files saved in `.analyze_cache/` directory
- **Task Isolation**: Each task has its own files, allowing parallel/serial analysis of multiple tasks without interference
- **Margin Isolation**: Each margin has its own comparison file, allowing comparison across different margins
- **Browser Compatibility**: HTML files work in any modern browser with no external dependencies
- **Notification**: Tool prints file locations to stderr when files are saved

**Rounding Rules:**
- **Memory**: 
  - Minimum: 64Mi
  - < 1Gi: Rounds UP to next increment of 64Mi (e.g., 65MB → 128Mi, 200MB → 256Mi, 735MB → 768Mi)
  - >= 1Gi: Rounds UP to next whole Gi (e.g., 1269MB → 2Gi, 2989MB → 3Gi)
- **CPU**:
  - Minimum: 50m
  - Always rounds UP to next increment of 50m (e.g., 51m → 100m, 243m → 250m, 459m → 500m)
  - Always outputs in millicore format (e.g., `5100m` for 5.1 cores)

**Technical Notes:**
- **Memory Metrics**: The tool uses `container_memory_working_set_bytes` for all memory calculations (max, p95, p90, median). This metric reflects actual memory usage, not memory limits. This ensures recommendations are based on real usage patterns rather than configured limits.
- **CPU Metrics**: CPU calculations use `container_cpu_usage_seconds_total` with rate calculations to determine actual CPU consumption over time.

Konflux Cluster Authentication
===================================

You can use 'oclogin' + 'oclogin-all' utilities shared by Jan Hutar.  
These automatically generate kubeconfig entries for all Konflux clusters.


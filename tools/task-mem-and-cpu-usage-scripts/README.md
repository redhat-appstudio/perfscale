Scripts for extracting Memory and CPU Usage Metrics
====================================================

This directory contains scripts for extracting memory and CPU usage metrics (Max, P95, P90, Median) for each `task` and `step` executed inside Konflux clusters. These scripts were created while addressing: https://issues.redhat.com/browse/KONFLUX-6712.

The workflow supports:
- **Multi-cluster execution** - Automatically iterates over all configured clusters
- **Memory metrics** - Max, P95, P90, and Median memory usage per task/step
- **CPU metrics** - Max, P95, P90, and Median CPU usage per task/step (NEW)
- **Optimized batching** - Handles unlimited pods efficiently using intelligent batching (NEW)
- **Long time ranges** - Supports 1 day to 30+ days with adaptive query optimization (NEW)
- **Task-scoped queries** - Ensures metrics are only from pods belonging to the specified task (NEW)
- **CSV / JSON / Colorized text output** - Multiple output formats
- **Per-pod attribution** - Identifies the specific pod with max memory and max CPU usage
- **Automatic metadata retrieval** - Namespace, Component, Application (where available)

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
             │        - container_memory_max_usage_bytes (peak memory)             │
             │        - container_memory_working_set_bytes (for percentiles)       │
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
```
"cluster","task","step","pod_max_memory","pod_namespace_mem","component","application","mem_max_mb","mem_p95_mb","mem_p90_mb","mem_median_mb","pod_max_cpu","pod_namespace_cpu","cpu_max","cpu_p95","cpu_p90","cpu_median"
"stone-prd-rh01","buildah","step-build","maestro-on-pull-request-wtpkk-build-container-pod","maestro-rhtap-tenant","N/A","N/A","8192","8191","8190","8183","operator-on-pull-request-45m69-build-container-pod","vp-operator-release-tenant","3569m","3569m","3569m","3569m"
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
- **Automatic Data Collection**: Can extract task/step info from YAML and automatically run data collection
- **Caching System**: Recommendations are cached, allowing review before applying changes
- **Comparison Tables**: Shows current vs proposed resource limits side-by-side
- **Configurable Base Metrics**: Choose calculation base (max, P95, P90, median) with configurable safety margin
- **Patch File Generation**: For remote GitHub URLs, generates `.patch` files for manual review
- **Smart Rounding**: 
  - Memory: Rounds UP to increments of 256Mi (< 1Gi) or whole Gi (>= 1Gi), minimum 256Mi
  - CPU: Rounds UP to increments of 100m, minimum 100m
- **Update from Cache**: Apply cached recommendations without re-running analysis

**Usage Examples:**

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

**4. Analyze with custom margin and base metric:**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --margin 5 --base p95 --days 10
```

**5. Two-step workflow (recommended for review):**
```bash
# Step 1: Generate and cache recommendations (shows table format output)
./analyze_resource_limits.py --file https://github.com/.../buildah.yaml --margin 5 --days 10

# Step 2: Review cached recommendations and apply (shows comparison table)
./analyze_resource_limits.py --update
```

**6. Update local YAML file directly:**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --update
```

**7. Update with specific parameters:**
```bash
./analyze_resource_limits.py --file /path/to/buildah.yaml --update --margin 10 --base max
```

**Command-line Options:**
- `--file FILE` - YAML file path or GitHub URL to analyze (auto-runs data collection)
- `--update` - Update the YAML file with recommended resource limits. If `--file` is not provided, uses cached recommendations from the last run
- `--margin MARGIN` - Safety margin percentage (default: 10)
- `--base {max,p95,p90,median}` - Base metric for margin calculation (default: max)
- `--days DAYS` - Number of days for data collection when using `--file` (default: 7)

**How it works:**

1. **With `--file` (local or GitHub URL):**
   - Extracts task name and step names from Tekton Task YAML
   - Extracts current resource limits for comparison
   - Automatically runs `wrapper_for_promql_for_all_clusters.sh` to collect data
   - Shows data collection output in table format
   - Analyzes data across all clusters for each step
   - Calculates recommendations using selected base metric + safety margin
   - Rounds values to standard Kubernetes resource sizes
   - Saves recommendations to cache
   - Shows detailed analysis and comparison table
   - If `--update` is used: Updates local YAML or generates patch file for remote URLs

2. **With piped input:**
   - Reads CSV data from stdin
   - Analyzes and provides recommendations
   - Does not cache (no file reference)

3. **With `--update` only (no `--file`):**
   - Loads most recent cached recommendations
   - Shows comparison table
   - Applies changes to the original file/URL

**Example Output:**

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
prepare-sboms             512Mi / 100m                   256Mi / 300m                   512Mi / null                   256Mi / 300m                  
upload-sbom               512Mi / 100m                   256Mi / 100m                   512Mi / null                   256Mi / 100m                  
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
- Each file/URL gets a unique cache file (MD5 hash of path/URL)
- Cache includes: recommendations, margin, base metric, days, and timestamp
- Most recent cache is used when running `--update` without `--file`

**Rounding Rules:**
- **Memory**: 
  - Minimum: 256Mi
  - < 1Gi: Rounds UP to next increment of 256Mi (e.g., 300MB → 512Mi, 544MB → 768Mi)
  - >= 1Gi: Rounds UP to next whole Gi (e.g., 1269MB → 2Gi)
- **CPU**:
  - Minimum: 100m
  - Always rounds UP to next increment of 100m (e.g., 243m → 300m, 385m → 400m)
  - Always outputs in millicore format (e.g., `5100m` for 5.1 cores)

Konflux Cluster Authentication
===================================

You can use 'oclogin' + 'oclogin-all' utilities shared by Jan Hutar.  
These automatically generate kubeconfig entries for all Konflux clusters.


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
          │       - Calls Python script to list pods (filtered by task label)       │
          │       - Processes pods in batches (50 pods per batch) to avoid limits   │
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
             │ • Uses adaptive step sizing based on time range:                     │
             │        - ≤1 day: 30s step                                           │
             │        - ≤7 days: 5m step                                           │
             │        - ≤30 days: 15m step                                         │
             │        - >30 days: 1h step                                           │
             │ • Sends HTTP requests to Prometheus API                             │
             │ • Executes queries for:                                              │
             │        - container_memory_max_usage_bytes (peak memory)              │
             │        - container_memory_working_set_bytes (for percentiles)       │
             │        - container_cpu_usage_seconds_total (with rate calculation)  │
             │ • Returns time series data for aggregation                           │
             └──────────────────────────────┬──────────────────────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────────────────┐
                     │             Aggregators in wrapper_for_promql.sh         │
                     │──────────────────────────────────────────────────────────│
                     │ • Processes pods in batches (50 per batch)              │
                     │ • For each batch:                                        │
                     │        - Queries memory max, p95, p90, median            │
                     │        - Queries CPU max, p95, p90, median              │
                     │        - Validates returned pods belong to task         │
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

    ./wrapper_for_promql_for_all_clusters.sh <num_of_days>

Example:

    ./wrapper_for_promql_for_all_clusters.sh 7

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

Output Modes
===================================

The wrapper supports:

--csv    : Machine-readable CSV output  
--json   : JSON document output  
--color  : Human-friendly colorized output  

Example:

    ./wrapper_for_promql_for_all_clusters.sh 1 --csv

CSV Output Format
===================================
The CSV output includes both memory and CPU metrics with separate pod attribution:

```
"cluster","task","step","pod_max_memory","pod_namespace_mem","component","application","mem_max_mb","mem_p95_mb","mem_p90_mb","mem_median_mb","pod_max_cpu","pod_namespace_cpu","cpu_max","cpu_p95","cpu_p90","cpu_median"
```

**Column Descriptions:**
- `pod_max_memory` - Pod name with the highest memory usage
- `pod_namespace_mem` - Namespace of the pod with max memory
- `component` - Component name for the max memory pod (if available)
- `application` - Application name for the max memory pod (if available)
- `mem_max_mb` - Maximum memory usage in MB
- `mem_p95_mb`, `mem_p90_mb`, `mem_median_mb` - Memory percentiles in MB
- `pod_max_cpu` - Pod name with the highest CPU usage
- `pod_namespace_cpu` - Namespace of the pod with max CPU
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
Konflux Cluster Authentication
===================================

You can use 'oclogin' + 'oclogin-all' utilities shared by Jan Hutar.  
These automatically generate kubeconfig entries for all Konflux clusters.


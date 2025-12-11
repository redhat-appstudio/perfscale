Scripts for extracting Memory Usage
===================================

This directory contains scripts for extracting memory usage metrics (Max, P95, P90, Median) for each `task` and `step` executed inside Konflux clusters. These scripts were created while addressing: https://issues.redhat.com/browse/KONFLUX-6712.

The workflow supports:
- Multi-cluster execution
- CSV / JSON / Colorized text output
- Per-pod memory usage attribution
- Automatic retrieval of Namespace, Component, Application (where available)

Architecture Overview (ASCII Diagram)
===================================

               +----------------------------------------+
               | wrapper_for_promql_for_all_clusters.sh |
               |   (loops through all Konflux clusters) |
               +-------------------------+--------------+
                                         |
                                         v
                         +---------------+----------------+
                         | Switch kube context to cluster |
                         +---------------+----------------+
                                         |
                                         v
                         +---------------+----------------+
                         |     wrapper_for_promql.sh      |
                         | (Runs per-task / per-step loop)|
                         +---------------+----------------+
                                         |
                                         v
       +---------------------------------+--------------------------------+
       |                                                                      |
       v                                                                      v
+---------------+                                              +-------------------------------+
| list_pods_for_a_particular_task.py                           | list_container_mem_usage_for_ |
| - Queries Prometheus for pods used                           |   a_particular_pod.py         |
|   by a given task/step                                       | - Fetches container_memory_*  |
+---------------+                                              | - Computes Max / P95 / P90    |
       |                                                       |   Median                      |
       v                                                       +-------------------------------+
       +-----------------------------+                          |
                               v                                 v
                   +----------------------------+   +-----------------------------+
                   | Output aggregator           |   | Formatters (CSV/JSON/Color)|
                   | Builds final result set     |-->| Produces final report      |
                   +----------------------------+   +-----------------------------+

How to Run
===================================

Run for last N days:

    ```./wrapper_for_promql_for_all_clusters.sh <num_of_days>```

Example:

    ```./wrapper_for_promql_for_all_clusters.sh 7```

The PromQL range window and sampling delta adjust automatically.

Python Virtual Environment
===================================

It is recommended to create a venv:

    ```python -m venv promql_for_mem_metrics
    source promql_for_mem_metrics/bin/activate```

Output Modes
===================================

The wrapper supports:

--csv    : Machine-readable CSV output  
--json   : JSON document output  
--color  : Human-friendly colorized output  

Example:

    ```./wrapper_for_promql_for_all_clusters.sh 1 --csv```

CSV Output Example
===================================
```
"cluster","task","step","max_mem_mb","pod","namespace","component","application","p95_mb","p90_mb","median_mb"
"kflux-prd-rh02","buildah","step-build","8192","crc-binary-on-pull-request-6kgk9-build-container-pod","crc-tenant","N/A","N/A","0","0","0"
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


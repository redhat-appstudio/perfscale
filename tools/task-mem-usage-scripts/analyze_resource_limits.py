#!/usr/bin/env python3
"""
Analyze resource consumption data and provide recommendations for resource limits.

Usage:
    # From piped input:
    ./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py

    # From YAML file (auto-runs data collection):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml
    ./analyze_resource_limits.py --file https://github.com/.../buildah.yaml

    # Update YAML file with recommendations:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --update
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

try:
    import requests
    import yaml
except ImportError as e:
    print(f"Error: Missing required library. Install with: pip install requests pyyaml", file=sys.stderr)
    sys.exit(1)


def convert_github_url_to_raw(url):
    """Convert GitHub blob URL to raw content URL."""
    # Convert blob URL to raw URL
    # https://github.com/user/repo/blob/branch/path -> https://raw.githubusercontent.com/user/repo/branch/path
    pattern = r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)'
    match = re.match(pattern, url)
    if match:
        user, repo, branch, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
    return url


def fetch_yaml_content(file_path_or_url):
    """Fetch YAML content from file path or URL."""
    if file_path_or_url.startswith('http://') or file_path_or_url.startswith('https://'):
        url = convert_github_url_to_raw(file_path_or_url)
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return yaml.safe_load(response.text), url
        except requests.RequestException as e:
            print(f"Error fetching URL {url}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        path = Path(file_path_or_url)
        if not path.exists():
            print(f"Error: File not found: {file_path_or_url}", file=sys.stderr)
            sys.exit(1)
        with open(path, 'r') as f:
            return yaml.safe_load(f), str(path.absolute())


def extract_task_info(yaml_content):
    """Extract task name and step names from Tekton Task YAML."""
    task_name = yaml_content.get('metadata', {}).get('name', '')
    steps = []
    
    # Extract step names from spec.steps
    for step in yaml_content.get('spec', {}).get('steps', []):
        step_name = step.get('name', '')
        if step_name:
            steps.append(step_name)
    
    # Also check stepTemplate for default resources
    step_template = yaml_content.get('spec', {}).get('stepTemplate', {})
    default_resources = step_template.get('computeResources', {})
    
    return task_name, steps, default_resources


def run_data_collection(task_name, steps, days=7):
    """Run the wrapper script to collect data."""
    script_path = Path(__file__).parent / 'wrapper_for_promql_for_all_clusters.sh'
    if not script_path.exists():
        print(f"Error: wrapper script not found: {script_path}", file=sys.stderr)
        sys.exit(1)
    
    # Create a temporary wrapper that sets TASK_NAME and STEPS
    temp_wrapper = script_path.parent / '.temp_wrapper.sh'
    try:
        with open(script_path, 'r') as f:
            wrapper_content = f.read()
        
        # Replace hardcoded TASK_NAME and STEPS
        # Steps in CSV are "step-{name}", so ensure we use that format
        wrapper_content = re.sub(
            r'TASK_NAME="[^"]*"',
            f'TASK_NAME="{task_name}"',
            wrapper_content
        )
        # Convert step names to "step-{name}" format for the wrapper
        steps_str = ' '.join(f'step-{s}' if not s.startswith('step-') else s for s in steps)
        wrapper_content = re.sub(
            r'STEPS="[^"]*"',
            f'STEPS="{steps_str}"',
            wrapper_content
        )
        
        with open(temp_wrapper, 'w') as f:
            f.write(wrapper_content)
        os.chmod(temp_wrapper, 0o755)
        
        # Run the wrapper
        print(f"Collecting data for task '{task_name}' with steps: {', '.join(steps)}", file=sys.stderr)
        print(f"Running: {temp_wrapper} {days} --csv", file=sys.stderr)
        
        result = subprocess.run(
            [str(temp_wrapper), str(days), '--csv'],
            capture_output=True,
            text=True,
            cwd=script_path.parent
        )
        
        if result.returncode != 0:
            print(f"Error running wrapper script:", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            sys.exit(1)
        
        return result.stdout
    finally:
        if temp_wrapper.exists():
            temp_wrapper.unlink()


def parse_csv_data(csv_text):
    """Parse CSV data from the wrapper script output."""
    data = []
    lines = [line for line in csv_text.strip().split('\n') if line.strip()]
    if not lines:
        return data
    
    reader = csv.DictReader(lines)
    for row in reader:
        # Clean up keys (remove spaces and quotes)
        cleaned_row = {k.strip().strip('"'): v.strip().strip('"') for k, v in row.items()}
        data.append(cleaned_row)
    return data


def round_memory_to_standard(mb):
    """Round memory to standard Kubernetes values.
    
    For values < 1Gi: round to nearest power of 2 (32Mi, 64Mi, 128Mi, 256Mi, 512Mi)
    For values >= 1Gi: round to whole Gi values (1Gi, 2Gi, 3Gi, etc.)
    
    Rounds to nearest, but ensures we don't go below the recommended value.
    """
    mb = float(mb)
    
    if mb < 1024:
        # Round to nearest power of 2: 32, 64, 128, 256, 512
        # Thresholds are midpoints between powers of 2
        if mb <= 48:
            rounded = 32
        elif mb <= 96:
            rounded = 64
        elif mb <= 192:
            rounded = 128
        elif mb <= 384:
            rounded = 256
        elif mb <= 768:
            rounded = 512
        else:
            # > 768Mi, round up to next Gi
            return 1024
        
        # Ensure we don't go below the recommended value
        if rounded < mb:
            # Round up to next power of 2
            if rounded == 32:
                rounded = 64
            elif rounded == 64:
                rounded = 128
            elif rounded == 128:
                rounded = 256
            elif rounded == 256:
                rounded = 512
            elif rounded == 512:
                rounded = 1024
        
        return rounded
    else:
        # Round to nearest whole Gi, but round up if fractional
        gi = mb / 1024.0
        rounded_gi = round(gi)
        # If rounding down would go below original, round up instead
        if rounded_gi * 1024 < mb:
            rounded_gi += 1
        return rounded_gi * 1024


def mb_to_kubernetes(mb):
    """Convert MB to Kubernetes memory format with standard rounding."""
    mb = float(mb)
    rounded_mb = round_memory_to_standard(mb)
    
    if rounded_mb < 1024:
        return f"{int(rounded_mb)}Mi"
    else:
        gi = rounded_mb / 1024.0
        # Should always be whole number after rounding
        return f"{int(gi)}Gi"


def round_cpu_to_standard(cores):
    """Round CPU to nearest millicore value.
    
    Always rounds to nearest millicore (e.g., 5.1 cores = 5100m).
    Rounds up to ensure we don't go below the recommended value.
    """
    cores = float(cores)
    millicores = cores * 1000
    
    # Round up to nearest millicore
    rounded_m = int(millicores) + (1 if (millicores % 1) > 0 else 0)
    return rounded_m / 1000.0


def cores_to_kubernetes(cores):
    """Convert cores to Kubernetes CPU format, always in millicores."""
    cores = float(cores)
    rounded_cores = round_cpu_to_standard(cores)
    
    # Always return as millicores
    millicores = int(rounded_cores * 1000)
    return f"{millicores}m"


def parse_cpu_value(cpu_str):
    """Parse CPU value from format like '3569m' or '4.5'."""
    if not cpu_str or cpu_str == '0m' or cpu_str == '0':
        return 0.0
    if cpu_str.endswith('m'):
        return float(cpu_str[:-1]) / 1000.0
    return float(cpu_str)


def analyze_step_data(step_name, step_rows, margin_pct=10):
    """Analyze data for a specific step and return recommendations."""
    if not step_rows:
        return None
    
    # Extract memory values
    mem_max_values = [float(r['mem_max_mb']) for r in step_rows if r.get('mem_max_mb') and r['mem_max_mb'] not in ('0', '', 'N/A')]
    mem_p95_values = [float(r['mem_p95_mb']) for r in step_rows if r.get('mem_p95_mb') and r['mem_p95_mb'] not in ('0', '', 'N/A')]
    mem_p90_values = [float(r['mem_p90_mb']) for r in step_rows if r.get('mem_p90_mb') and r['mem_p90_mb'] not in ('0', '', 'N/A')]
    mem_median_values = [float(r['mem_median_mb']) for r in step_rows if r.get('mem_median_mb') and r['mem_median_mb'] not in ('0', '', 'N/A')]
    
    # Extract CPU values
    cpu_max_values = [parse_cpu_value(r.get('cpu_max', '0m')) for r in step_rows if r.get('cpu_max')]
    cpu_p95_values = [parse_cpu_value(r.get('cpu_p95', '0m')) for r in step_rows if r.get('cpu_p95')]
    cpu_p90_values = [parse_cpu_value(r.get('cpu_p90', '0m')) for r in step_rows if r.get('cpu_p90')]
    cpu_median_values = [parse_cpu_value(r.get('cpu_median', '0m')) for r in step_rows if r.get('cpu_median')]
    
    if not mem_max_values:
        return None
    
    # Calculate max across all clusters
    mem_max_max = max(mem_max_values)
    mem_p95_max = max(mem_p95_values) if mem_p95_values else 0
    mem_p90_max = max(mem_p90_values) if mem_p90_values else 0
    mem_median_max = max(mem_median_values) if mem_median_values else 0
    
    cpu_max_max = max(cpu_max_values) if cpu_max_values else 0
    cpu_p95_max = max(cpu_p95_values) if cpu_p95_values else 0
    cpu_p90_max = max(cpu_p90_values) if cpu_p90_values else 0
    cpu_median_max = max(cpu_median_values) if cpu_median_values else 0
    
    # Calculate recommendations: P95 + margin, but don't exceed max observed
    mem_recommended = min(mem_max_max, int(mem_p95_max * (1 + margin_pct / 100))) if mem_p95_max > 0 else mem_max_max
    if cpu_p95_max > 0:
        cpu_recommended = min(cpu_max_max * 1.1, cpu_p95_max * (1 + margin_pct / 100))
    else:
        cpu_recommended = cpu_max_max if cpu_max_max > 0 else 0
    
    # Count coverage
    mem_coverage = len([x for x in mem_max_values if x <= mem_recommended])
    cpu_coverage = len([x for x in cpu_max_values if x <= cpu_recommended]) if cpu_max_values else 0
    
    return {
        'step_name': step_name,
        'mem_recommended_mb': mem_recommended,
        'mem_recommended_k8s': mb_to_kubernetes(mem_recommended),
        'cpu_recommended_cores': cpu_recommended,
        'cpu_recommended_k8s': cores_to_kubernetes(cpu_recommended),
        'mem_max_max': mem_max_max,
        'mem_p95_max': mem_p95_max,
        'cpu_max_max': cpu_max_max,
        'cpu_p95_max': cpu_p95_max,
        'mem_coverage': mem_coverage,
        'mem_total': len(mem_max_values),
        'cpu_coverage': cpu_coverage,
        'cpu_total': len(cpu_max_values) if cpu_max_values else 0,
    }


def print_analysis(recommendations, margin_pct):
    """Print analysis results."""
    print("=" * 80)
    print(f"RESOURCE LIMIT RECOMMENDATIONS (P95 + {margin_pct}% Safety Margin)")
    print("=" * 80)
    print()
    
    for rec in recommendations:
        if rec is None:
            continue
        
        print(f"Step: {rec['step_name']}")
        print("-" * 80)
        print(f"  Memory: {rec['mem_recommended_k8s']}")
        print(f"    - Max observed: {mb_to_kubernetes(rec['mem_max_max'])}")
        print(f"    - P95 max: {mb_to_kubernetes(rec['mem_p95_max'])}")
        print(f"    - Coverage: {rec['mem_coverage']}/{rec['mem_total']} clusters")
        print()
        
        if rec['cpu_total'] > 0:
            print(f"  CPU: {rec['cpu_recommended_k8s']}")
            print(f"    - Max observed: {cores_to_kubernetes(rec['cpu_max_max'])}")
            print(f"    - P95 max: {cores_to_kubernetes(rec['cpu_p95_max'])}")
            print(f"    - Coverage: {rec['cpu_coverage']}/{rec['cpu_total']} clusters")
        else:
            print(f"  CPU: No data available")
        print()


def update_yaml_file(yaml_path, recommendations, original_yaml):
    """Update YAML file with recommended resource limits."""
    updated = False
    
    for rec in recommendations:
        if rec is None:
            continue
        
        step_name_csv = rec['step_name']  # e.g., "step-build"
        mem_k8s = rec['mem_recommended_k8s']
        cpu_k8s = rec['cpu_recommended_k8s']
        
        # Convert "step-build" to "build" for YAML matching
        step_name_yaml = step_name_csv.replace('step-', '') if step_name_csv.startswith('step-') else step_name_csv
        
        # Find the step in the YAML
        steps = original_yaml.get('spec', {}).get('steps', [])
        for step in steps:
            if step.get('name') == step_name_yaml:
                # Update or create computeResources
                if 'computeResources' not in step:
                    step['computeResources'] = {}
                
                if 'limits' not in step['computeResources']:
                    step['computeResources']['limits'] = {}
                if 'requests' not in step['computeResources']:
                    step['computeResources']['requests'] = {}
                
                step['computeResources']['limits']['memory'] = mem_k8s
                step['computeResources']['limits']['cpu'] = cpu_k8s
                step['computeResources']['requests']['memory'] = mem_k8s
                step['computeResources']['requests']['cpu'] = cpu_k8s
                
                updated = True
                print(f"Updated step '{step_name_yaml}': memory={mem_k8s}, cpu={cpu_k8s}", file=sys.stderr)
                break
    
    return updated


def main():
    parser = argparse.ArgumentParser(
        description='Analyze resource consumption and provide recommendations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From piped input:
  ./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py

  # From YAML file (auto-runs data collection):
  ./analyze_resource_limits.py --file /path/to/buildah.yaml
  ./analyze_resource_limits.py --file https://github.com/.../buildah.yaml

  # Update YAML file with recommendations:
  ./analyze_resource_limits.py --file /path/to/buildah.yaml --update
        """
    )
    parser.add_argument(
        '--file',
        help='YAML file path or GitHub URL to analyze (auto-runs data collection)'
    )
    parser.add_argument(
        '--update',
        action='store_true',
        help='Update the YAML file with recommended resource limits'
    )
    parser.add_argument(
        '--margin',
        type=int,
        default=10,
        help='Safety margin percentage (default: 10)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days for data collection (default: 7)'
    )
    
    args = parser.parse_args()
    
    # Determine input source
    if args.file:
        # Load YAML and extract task info
        yaml_content, yaml_path = fetch_yaml_content(args.file)
        task_name, steps, default_resources = extract_task_info(yaml_content)
        
        if not task_name:
            print("Error: Could not extract task name from YAML", file=sys.stderr)
            sys.exit(1)
        
        if not steps:
            print("Error: Could not extract steps from YAML", file=sys.stderr)
            sys.exit(1)
        
        # Run data collection
        csv_data = run_data_collection(task_name, steps, args.days)
    else:
        # Read from stdin
        if sys.stdin.isatty():
            print("Error: No input provided. Use --file or pipe CSV data.", file=sys.stderr)
            sys.exit(1)
        csv_data = sys.stdin.read()
        yaml_content = None
        yaml_path = None
        task_name = None
        steps = None
    
    # Parse CSV data
    data = parse_csv_data(csv_data)
    if not data:
        print("Error: No data found in CSV input", file=sys.stderr)
        sys.exit(1)
    
    # Group by step
    by_step = defaultdict(list)
    for row in data:
        step = row.get('step', '').strip()
        if step:
            by_step[step].append(row)
    
    # Analyze each step
    recommendations = []
    for step_name in sorted(by_step.keys()):
        rec = analyze_step_data(step_name, by_step[step_name], args.margin)
        if rec:
            recommendations.append(rec)
    
    # Print analysis
    print_analysis(recommendations, args.margin)
    
    # Update YAML if requested
    if args.update and yaml_path and not yaml_path.startswith('http'):
        updated = update_yaml_file(yaml_path, recommendations, yaml_content)
        if updated:
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f, default_flow_style=False, sort_keys=False, width=120)
            print(f"\nUpdated YAML file: {yaml_path}", file=sys.stderr)
        else:
            print("Warning: No steps were updated in YAML file", file=sys.stderr)
    elif args.update and yaml_path and yaml_path.startswith('http'):
        print("Error: Cannot update remote file. Please use a local file path.", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()


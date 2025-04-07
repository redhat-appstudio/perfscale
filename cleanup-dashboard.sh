#!/bin/bash

set -eu

if ! type -p jq >/dev/null; then
    echo "ERROR: No 'jq' available" >&2
    exit 1
fi

dashboard=$1

if [[ ! -r "$dashboard" ]]; then
    echo "ERROR: Can not read dashboard '$dashboard'" >&2
    exit 1
fi

dashboard_tmp=$( mktemp)

jq 'walk(if type == "object" then with_entries(select(.key | test("datasource") | not)) else . end)' "$dashboard" >"$dashboard_tmp"
cp "$dashboard_tmp" "$dashboard"

echo "Cleaned dashboard '$dashboard'"

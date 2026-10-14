#!/usr/bin/env bash
# deploy/grafana/dashboards is the source of truth; Helm can only read files inside the chart directory,
# so the chart carries copies. CI fails (diff -r) when they drift: run this after editing a dashboard.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p deploy/helm/slm-studio/dashboards
cp -f deploy/grafana/dashboards/*.json deploy/helm/slm-studio/dashboards/
diff -r deploy/grafana/dashboards deploy/helm/slm-studio/dashboards && echo "dashboards in sync"

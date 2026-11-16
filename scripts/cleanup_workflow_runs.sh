#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

# --paginate visits every page; only completed runs with failed/cancelled
# conclusions are selected. Successful and in-progress runs are untouched.
run_ids="$(gh api --paginate "/repos/${GITHUB_REPOSITORY}/actions/runs?status=completed&per_page=100" \
  --jq '.workflow_runs[] | select(.conclusion == "failure" or .conclusion == "cancelled") | .id')"

deleted=0
while IFS= read -r run_id; do
  [[ -n "${run_id}" ]] || continue
  gh api --method DELETE "/repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}"
  printf 'Deleted completed failed/cancelled workflow run %s\n' "${run_id}"
  deleted=$((deleted + 1))
done <<< "${run_ids}"

printf 'Workflow history cleanup complete; deleted %s failed/cancelled run(s).\n' "${deleted}"

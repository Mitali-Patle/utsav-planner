#!/usr/bin/env bash
# Block pushes when the repository, including history, exceeds the size budget.
set -euo pipefail

limit_kib="${REPO_SIZE_LIMIT_KIB:-8192}"
read -r loose packed < <(git count-objects -v | awk '/^size:/ {l=$2} /^size-pack:/ {p=$2} END {print l, p}')
total=$((loose + packed))

if (( total > limit_kib )); then
  echo "Blocked: repository is ${total} KiB, over the ${limit_kib} KiB budget (hard limit 10 MB)." >&2
  exit 1
fi
echo "Repository size OK: ${total} KiB of ${limit_kib} KiB."

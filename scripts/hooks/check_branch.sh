#!/usr/bin/env bash
# Block commits and pushes from any branch other than main (single-branch repo rule).
set -euo pipefail

branch="$(git symbolic-ref --quiet --short HEAD || echo "DETACHED")"
if [[ "$branch" != "main" ]]; then
  echo "Blocked: this repo allows work on 'main' only (current: $branch)." >&2
  exit 1
fi

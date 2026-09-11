#!/usr/bin/env bash
# Run from a checkout of SOURCE_SHA with only scripts/source.json modified.
set -euo pipefail
: "${SOURCE_SHA:?SOURCE_SHA is required}"
branch="daily"

git fetch origin develop
if [[ "$(git rev-parse origin/develop)" != "${SOURCE_SHA}" ]]; then
  echo "develop advanced; refusing a stale update." >&2
  exit 1
fi
if [[ "$(git rev-parse HEAD)" != "${SOURCE_SHA}" ]]; then
  echo "Checkout must be at SOURCE_SHA." >&2
  exit 1
fi
# Capture the expected remote SHA before preparing the commit. An absent branch
# uses an empty explicit lease, which only permits creation.
expected="$(git ls-remote --heads origin "refs/heads/${branch}" | cut -f1)"
if [[ -n "${expected}" ]]; then
  git fetch origin "refs/heads/${branch}"
  if [[ "$(git rev-parse FETCH_HEAD)" != "${expected}" ]]; then
    echo "daily changed during preparation; retry the update." >&2
    exit 1
  fi
  if [[ "$(git rev-parse "${expected}^")" == "${SOURCE_SHA}" ]] && \
      git show "${expected}:scripts/source.json" | cmp -s - scripts/source.json; then
    echo "daily already contains this candidate; leaving it untouched."
    exit 0
  fi
fi

git config user.name "neat-releases[bot]"
git config user.email "neat-releases[bot]@users.noreply.github.com"
git switch -C "${branch}"
git add scripts/source.json
changed_files="$(git diff --cached --name-only)"
if [[ -n "${changed_files}" && "${changed_files}" != "scripts/source.json" ]]; then
  echo "Only scripts/source.json may be committed." >&2
  exit 1
fi
git commit --allow-empty -m "Update Model Compiler component versions" \
  -m "Component-Updater: daily"
git push "--force-with-lease=refs/heads/${branch}:${expected}" origin "HEAD:refs/heads/${branch}"

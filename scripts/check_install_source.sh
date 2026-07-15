#!/bin/sh
set -eu

ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
UPSTREAM_REF="${SMART_HOME_INSTALL_UPSTREAM_REF:-origin/main}"

fail() {
  printf 'Install blocked: %s\n' "$1" >&2
  return "$2"
}

if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  fail "${ROOT} is not a Git worktree." 2
  exit $?
fi

if ! git -C "$ROOT" rev-parse --verify --quiet "${UPSTREAM_REF}^{commit}" >/dev/null; then
  if [ "${SMART_HOME_ALLOW_UNVERIFIED_INSTALL:-0}" != "1" ]; then
    fail "cannot verify ${UPSTREAM_REF}. Fetch it first or set SMART_HOME_ALLOW_UNVERIFIED_INSTALL=1." 2
    exit $?
  fi
elif [ "$(git -C "$ROOT" rev-list --count "HEAD..${UPSTREAM_REF}")" -gt 0 ]; then
  if [ "${SMART_HOME_ALLOW_STALE_INSTALL:-0}" != "1" ]; then
    fail "this checkout is missing commits from ${UPSTREAM_REF}. Rebase/update it or set SMART_HOME_ALLOW_STALE_INSTALL=1." 3
    exit $?
  fi
fi

if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ] &&
   [ "${SMART_HOME_ALLOW_DIRTY_INSTALL:-0}" != "1" ]; then
  fail "the source worktree has tracked or untracked changes. Commit/stash them or set SMART_HOME_ALLOW_DIRTY_INSTALL=1." 4
  exit $?
fi

printf 'Install source verified: %s\n' "$ROOT"

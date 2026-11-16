#!/usr/bin/env bash
# Smoke test a deployed preview/staging/production URL. Every deploy job runs this as
# its final step and fails if it fails (ADR 0011).
#
# Usage: scripts/smoke.sh <base-url>        (or: make smoke URL=<base-url>)
#
# 1. Health: GET <base-url>/health must return 200. If /health returns 404 (a static
#    site), GET <base-url>/ must return 200 instead.
# 2. Every route listed in smoke-routes.txt (one path per line, # comments) must return 200.
#
# Each request has a 10 s time limit. Connection errors, timeouts and 5xx responses are
# retried up to 5 times with exponential backoff (1, 2, 4, 8, 16 s); other status codes
# fail at once. Each check (all its attempts plus backoff) stops within a 50 s budget, and
# routes are skipped once health fails, so an unreachable URL fails in under a minute.
# Prints a summary table and exits non-zero if any check fails.
#
# Security: requests carry no auth headers, cookies or secrets, and ~/.curlrc is ignored.
#
# Tuning (environment): SMOKE_ROUTES_FILE (default: <repo>/smoke-routes.txt),
# SMOKE_TIMEOUT (10), SMOKE_RETRIES (5), SMOKE_BACKOFF (1, first backoff in seconds),
# SMOKE_BUDGET (50, seconds per check).
set -uo pipefail

usage() { echo "usage: $0 <base-url>   e.g. $0 https://preview.example.com" >&2; exit 2; }

[ $# -eq 1 ] || usage
BASE_URL="${1%/}"
case "$BASE_URL" in
  http://?*|https://?*) ;;
  *) echo "smoke: base URL must start with http:// or https://, got '$1'" >&2; usage ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTES_FILE="${SMOKE_ROUTES_FILE:-$SCRIPT_DIR/../smoke-routes.txt}"
TIMEOUT="${SMOKE_TIMEOUT:-10}"
RETRIES="${SMOKE_RETRIES:-5}"
BACKOFF="${SMOKE_BACKOFF:-1}"
BUDGET="${SMOKE_BUDGET:-50}"

for name in TIMEOUT RETRIES BACKOFF BUDGET; do
  [[ "${!name}" =~ ^[0-9]+$ ]] || { echo "smoke: SMOKE_$name must be a whole number" >&2; exit 2; }
done

# One GET with a time limit in seconds; prints the HTTP status code ("000" when no response).
fetch() {
  local code
  code="$(curl -q --silent --location --max-time "$2" --output /dev/null \
    --write-out '%{http_code}' "$BASE_URL$1" 2>/dev/null)"
  echo "${code:-000}"
}

# GET with retries for connection errors, timeouts and 5xx; prints the final status code.
# All attempts and backoff for one check fit in BUDGET seconds, so a dead or hanging host
# fails well within a minute.
fetch_with_retry() {
  local path="$1" attempt=0 delay="$BACKOFF" code start="$SECONDS" left limit
  while :; do
    left=$((BUDGET - (SECONDS - start)))
    limit=$(( left < TIMEOUT ? left : TIMEOUT ))
    [ "$limit" -ge 1 ] || limit=1
    code="$(fetch "$path" "$limit")"
    case "$code" in 000|5??) ;; *) break ;; esac
    [ "$attempt" -lt "$RETRIES" ] || break
    [ $((SECONDS - start + delay)) -lt "$BUDGET" ] || break
    attempt=$((attempt + 1))
    echo "smoke: $path -> $code, retry $attempt/$RETRIES in ${delay}s" >&2
    sleep "$delay"
    delay=$((delay * 2))
  done
  echo "$code"
}

ROUTES=()
STATUSES=()
RESULTS=()
FAILED=0

record() {
  ROUTES+=("$1"); STATUSES+=("$2"); RESULTS+=("$3")
  [ "$3" != FAIL ] || FAILED=1
}

check() {
  local code
  code="$(fetch_with_retry "$1")"
  if [ "$code" = 200 ]; then record "$1" "$code" PASS; else record "$1" "$code" FAIL; fi
}

# 1. Health check, falling back to / for static sites without /health.
health_code="$(fetch_with_retry /health)"
if [ "$health_code" = 404 ]; then
  record /health 404 "SKIP (static)"
  check /
elif [ "$health_code" = 200 ]; then
  record /health "$health_code" PASS
else
  record /health "$health_code" FAIL
fi
healthy=$((1 - FAILED))

# 2. Listed routes (skipped when the service is not healthy).
if [ -f "$ROUTES_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    route="${line%%#*}"
    route="$(echo "$route" | tr -d '[:space:]')"
    [ -n "$route" ] || continue
    case "$route" in /*) ;; *) route="/$route" ;; esac
    if [ "$healthy" = 1 ]; then check "$route"; else record "$route" - "SKIP (unhealthy)"; fi
  done < "$ROUTES_FILE"
else
  echo "smoke: no routes file at $ROUTES_FILE; checking health only" >&2
fi

echo "smoke summary for $BASE_URL"
printf '%-40s %-6s %s\n' ROUTE STATUS RESULT
for i in "${!ROUTES[@]}"; do
  printf '%-40s %-6s %s\n' "${ROUTES[$i]}" "${STATUSES[$i]}" "${RESULTS[$i]}"
done

if [ "$FAILED" -ne 0 ]; then
  echo "smoke: FAIL"
  exit 1
fi
echo "smoke: PASS"

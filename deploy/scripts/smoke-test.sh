#!/usr/bin/env bash
# Post-deploy smoke test for SLM Studio.
#
#   smoke-test.sh <api-base-url> [api-key]
#
# Checks, with retries (the rollout may still be settling):
#   1. GET  /readyz        -> 200                      (gateway and its dependencies are ready)
#   2. GET  /v1/models     -> 200, OpenAI list object  (auth + registry + route resolution work end to end)
#   3. POST /v1/chat/completions (only if SMOKE_MODEL is set) -> 200 with a choice
#
# Env: SMOKE_API_KEY (alternative to arg 2), SMOKE_MODEL, SMOKE_ATTEMPTS (default 30), SMOKE_SLEEP (default 10 s),
#      SMOKE_REQUIRE_MODELS=1 to fail when /v1/models returns an empty list.
# Exit: 0 ok | 1 check failed after all attempts | 2 usage error
set -euo pipefail

BASE="${1:-}"
KEY="${2:-${SMOKE_API_KEY:-}}"
ATTEMPTS="${SMOKE_ATTEMPTS:-30}"
SLEEP="${SMOKE_SLEEP:-10}"

if [[ -z "$BASE" ]]; then echo "usage: $0 <api-base-url> [api-key]" >&2; exit 2; fi
if [[ -z "$KEY" ]]; then echo "error: an API key is required for /v1/models (arg 2 or SMOKE_API_KEY)" >&2; exit 2; fi
BASE="${BASE%/}"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

retry() {  # retry <description> <command...>
  local what="$1"; shift
  local n=1
  until "$@"; do
    if (( n >= ATTEMPTS )); then log "FAIL  $what (after $n attempts)"; return 1; fi
    log "wait  $what (attempt $n/$ATTEMPTS failed; retrying in ${SLEEP}s)"
    n=$((n + 1)); sleep "$SLEEP"
  done
  log "OK    $what"
}

http_code() {  # http_code <curl args...>  -> prints status, body in $body
  curl -sS -o "$body" -w '%{http_code}' --max-time 20 "$@" 2>/dev/null || echo "000"
}

json_ok() {  # json_ok <jq filter> <grep -E fallback>
  if command -v jq >/dev/null 2>&1; then jq -e "$1" "$body" >/dev/null 2>&1; else grep -Eq "$2" "$body"; fi
}

check_readyz() { [[ "$(http_code "$BASE/readyz")" == "200" ]]; }

check_models() {
  [[ "$(http_code -H "Authorization: Bearer $KEY" "$BASE/v1/models")" == "200" ]] || return 1
  json_ok '.object == "list" and (.data | type == "array")' '"object"[[:space:]]*:[[:space:]]*"list"' || return 1
  if [[ "${SMOKE_REQUIRE_MODELS:-0}" == "1" ]]; then
    json_ok '(.data | length) > 0' '"id"[[:space:]]*:' || return 1
  fi
}

check_chat() {
  local payload
  payload="$(printf '{"model":"%s","messages":[{"role":"user","content":"ping"}],"max_tokens":8,"stream":false}' "$SMOKE_MODEL")"
  [[ "$(http_code -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d "$payload" "$BASE/v1/chat/completions")" == "200" ]] || return 1
  json_ok '.choices | length > 0' '"choices"[[:space:]]*:[[:space:]]*\[' || return 1
}

retry "GET  $BASE/readyz" check_readyz
retry "GET  $BASE/v1/models" check_models
if [[ -n "${SMOKE_MODEL:-}" ]]; then
  retry "POST $BASE/v1/chat/completions (model=$SMOKE_MODEL)" check_chat
fi
log "smoke test passed"

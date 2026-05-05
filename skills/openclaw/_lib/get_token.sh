#!/bin/bash
# skills/openclaw/_lib/get_token.sh — shared OAuth-token helper.
#
# Glad-Labs/poindexter#246 (provisioning) + #249 (Phase 3 cleanup).
# OAuth 2.1 Client Credentials mint against the worker's /token endpoint.
# The legacy ${POINDEXTER_KEY} static-Bearer fallback was removed in #249.
#
# Usage (sourced):
#
#   . "$(dirname "$0")/../../_lib/get_token.sh"
#   TOKEN="$(get_poindexter_token)" || exit 1
#   curl -H "Authorization: Bearer ${TOKEN}" "${FASTAPI_URL}/api/tasks"
#
# Resolution:
#
#   1. POINDEXTER_OAUTH_CLIENT_ID + POINDEXTER_OAUTH_CLIENT_SECRET env vars
#      → mint a fresh JWT via /token (cached under ~/.openclaw/.token-cache
#      until 30s before exp)
#   2. Otherwise, fail loud with a pointer to `poindexter auth migrate-openclaw`.
#
# Provision OAuth credentials with:
#
#   poindexter auth migrate-openclaw
#
# Then paste the printed env block into ~/.openclaw/openclaw.json so
# every skill subprocess inherits both vars.

# Bash 3.2-compatible (no associative arrays). The script itself
# doesn't run anything on source — it only defines functions.

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_oauth_cache_path() {
    # Per-client cache file so multiple OpenClaw setups on the same box
    # don't trample each other.
    local cache_dir="${HOME}/.openclaw"
    mkdir -p "$cache_dir" 2>/dev/null || cache_dir="/tmp"
    local cid="${POINDEXTER_OAUTH_CLIENT_ID:-default}"
    echo "${cache_dir}/.token-cache-${cid}"
}

_oauth_decode_exp() {
    # Decode a JWT's "exp" claim. Echoes the integer epoch on success,
    # nothing on failure. Pure shell + python — no jq dependency.
    local token="$1"
    [ -z "$token" ] && return 0
    local payload
    payload=$(echo "$token" | awk -F. '{print $2}')
    [ -z "$payload" ] && return 0
    # urlsafe-base64 → padded base64 → JSON → exp
    python -c "
import base64, json, sys
p = sys.argv[1]
p += '=' * (-len(p) % 4)
try:
    d = json.loads(base64.urlsafe_b64decode(p))
    exp = d.get('exp')
    if isinstance(exp, (int, float)):
        print(int(exp))
except Exception:
    pass
" "$payload" 2>/dev/null
}

_oauth_token_is_fresh() {
    # 0 (true) when cached token has > 30s left until exp.
    local token="$1"
    local exp
    exp=$(_oauth_decode_exp "$token")
    [ -z "$exp" ] && return 1
    local now
    now=$(date +%s)
    [ $((exp - now)) -gt 30 ]
}

_oauth_mint_token() {
    # POST /token with client_credentials grant. Echoes the access_token
    # on success, returns non-zero (and prints to stderr) on failure.
    local fastapi_url="${FASTAPI_URL:-http://localhost:8002}"
    local cid="$POINDEXTER_OAUTH_CLIENT_ID"
    local csec="$POINDEXTER_OAUTH_CLIENT_SECRET"
    if [ -z "$cid" ] || [ -z "$csec" ]; then
        echo "get_token: POINDEXTER_OAUTH_CLIENT_ID/SECRET not set" >&2
        return 1
    fi
    local resp http_code body
    resp=$(curl -s -w "\n%{http_code}" -X POST "${fastapi_url%/}/token" \
        -d "grant_type=client_credentials" \
        --data-urlencode "client_id=${cid}" \
        --data-urlencode "client_secret=${csec}" 2>&1)
    http_code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | sed '$d')
    if [ "$http_code" != "200" ]; then
        echo "get_token: /token returned HTTP $http_code: $body" >&2
        return 1
    fi
    # Extract access_token without jq
    echo "$body" | python -c "
import json, sys
try:
    d = json.load(sys.stdin)
    t = d.get('access_token')
    if t:
        print(t)
        sys.exit(0)
except Exception as e:
    print(f'parse failure: {e}', file=sys.stderr)
sys.exit(1)
" || return 1
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

get_poindexter_token() {
    # Echo a Bearer-eligible JWT to stdout. The legacy
    # ${POINDEXTER_KEY} / ${GLADLABS_KEY} static-Bearer fallback was
    # removed in Phase 3 (#249) — OAuth credentials are required.
    if [ -n "$POINDEXTER_OAUTH_CLIENT_ID" ] && [ -n "$POINDEXTER_OAUTH_CLIENT_SECRET" ]; then
        local cache_path cached new
        cache_path=$(_oauth_cache_path)
        if [ -f "$cache_path" ]; then
            cached=$(cat "$cache_path" 2>/dev/null)
            if _oauth_token_is_fresh "$cached"; then
                echo "$cached"
                return 0
            fi
        fi
        new=$(_oauth_mint_token) || return 1
        # Best-effort write — if HOME isn't writable just skip caching.
        echo "$new" > "$cache_path" 2>/dev/null && chmod 600 "$cache_path" 2>/dev/null
        echo "$new"
        return 0
    fi

    echo "get_token: no auth configured." >&2
    echo "  Run 'poindexter auth migrate-openclaw' to provision OAuth creds." >&2
    echo "  The legacy POINDEXTER_KEY static-Bearer path was removed in #249." >&2
    return 1
}

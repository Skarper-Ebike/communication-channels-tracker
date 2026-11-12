#!/usr/bin/env bash
# usage: bc.sh <https URL>   prints JSON, then a final line "NEXT: <url>" if there is a next page
set -euo pipefail
d="${TMPDIR:-/tmp}/bcauth"; mkdir -p "$d"; chmod 700 "$d"
UA="Mareks daily brief (mareks@skarper.com)"
if [ ! -s "$d/at" ]; then
  tok=$(curl -sS -f -A "$UA" -X POST https://launchpad.37signals.com/authorization/token \
    --data-urlencode type=refresh \
    --data-urlencode "refresh_token=$BASECAMP_REFRESH_TOKEN" \
    --data-urlencode "client_id=$BASECAMP_CLIENT_ID" \
    --data-urlencode "client_secret=$BASECAMP_CLIENT_SECRET" | jq -r '.access_token // empty')
  [ -n "$tok" ] || { echo "Basecamp token refresh failed" >&2; exit 1; }
  umask 077; printf %s "$tok" > "$d/at"
fi
curl -sS -f -A "$UA" -H "Authorization: Bearer $(cat "$d/at")" -D "$d/h" "$1"
echo; grep -i '^link:' "$d/h" | grep -o '<[^>]*>; rel="next"' | sed 's/^<\(.*\)>.*/NEXT: \1/' || true

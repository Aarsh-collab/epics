#!/bin/bash
# Offline selector integration checks; no real Tailscale/Docker/network calls.
set -euo pipefail
SCRIPTS=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export EPICS_APP_DIR="$TMP/app" TRACE="$TMP/trace"
mkdir -p "$TMP/mock" "$EPICS_APP_DIR/bin"
export PATH="$TMP/mock:$PATH"
cat > "$TMP/mock/docker" <<'MOCK'
#!/bin/bash
printf 'docker %s\n' "$*" >> "$TRACE"
case "$1" in
  logs) [[ ${NO_QUICK_URL:-0} == 1 ]] || echo 'https://offline-test.trycloudflare.com' ;;
esac
MOCK
cat > "$TMP/mock/python3" <<'MOCK'
#!/bin/bash
printf 'python3 %s\n' "$*" >> "$TRACE"
[[ ${FAIL_FUNNEL:-0} == 0 ]] || exit 1
if [[ "$2" == off ]]; then rm -f "$EPICS_APP_DIR/funnel-state.json"; fi
MOCK
printf '#!/bin/bash\nexit 0\n' > "$TMP/mock/sleep"
chmod +x "$TMP/mock/"*
expect_failure() {
    if bash "$SCRIPTS/tunnel.sh" "$@" > "$TMP/out" 2>&1; then
        echo "Expected failure" >&2; exit 1
    fi
}
: > "$TRACE"
# No opt-in: unchanged quick mode, even if an old Cloudflare token exists.
touch "$EPICS_APP_DIR/cloudflare-token"
bash "$SCRIPTS/tunnel.sh" > "$TMP/out"
grep -F -- '--url http://127.0.0.1:8090' "$TRACE" >/dev/null
[[ $(cat "$EPICS_APP_DIR/public-url") == https://offline-test.trycloudflare.com ]]
! grep -F python3 "$TRACE" >/dev/null
# Local plain-data configuration dispatches Funnel explicitly.
printf 'funnel\n' > "$EPICS_APP_DIR/tunnel-mode"
: > "$TRACE"
bash "$SCRIPTS/tunnel.sh" > "$TMP/out"
grep -F 'funnel.py start' "$TRACE" >/dev/null
! grep -F docker "$TRACE" >/dev/null
# Failed Funnel never launches a new random URL.
: > "$TRACE"
export FAIL_FUNNEL=1
expect_failure
! grep -F docker "$TRACE" >/dev/null
unset FAIL_FUNNEL
# Explicit quick rollback disables owned Funnel before creating quick tunnel.
touch "$EPICS_APP_DIR/funnel-state.json"
: > "$TRACE"
bash "$SCRIPTS/tunnel.sh" --quick > "$TMP/out"
head -1 "$TRACE" | grep -F 'funnel.py off' >/dev/null
grep -F 'docker run' "$TRACE" >/dev/null
[[ ! -e "$EPICS_APP_DIR/funnel-state.json" ]]
# Failed ownership/off check must stop rollback without touching Docker.
touch "$EPICS_APP_DIR/funnel-state.json"
: > "$TRACE"
export FAIL_FUNNEL=1
expect_failure --quick
! grep -F docker "$TRACE" >/dev/null
unset FAIL_FUNNEL
rm "$EPICS_APP_DIR/funnel-state.json"
printf 'invalid\n' > "$EPICS_APP_DIR/tunnel-mode"
: > "$TRACE"
expect_failure
[[ ! -s "$TRACE" ]]
expect_failure --unknown
[[ ! -s "$TRACE" ]]
# A quick launch timeout clears the previous URL.
printf 'quick\n' > "$EPICS_APP_DIR/tunnel-mode"
export NO_QUICK_URL=1
expect_failure
[[ ! -e "$EPICS_APP_DIR/public-url" ]]
unset NO_QUICK_URL
echo 'Offline tunnel selector checks passed.'


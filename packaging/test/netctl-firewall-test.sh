#!/usr/bin/env bash
# Isolated test for the wattpost-netctl firewall logic (Phase B, cloud#15).
#
# Runs the helper in an *unprivileged user+net namespace* — no root, no Pi —
# and asserts the nftables ruleset across the apply matrix, including the
# atomic-replace idempotency the helper relies on. A stub `systemctl` on PATH
# means the host's real sshd is never touched.
#
# Covers the arch-independent core of docs/firewall-test-plan.md. The
# WiFi-client / hotspot-AP / real-mDNS modes still need a real Pi.
#
# Run:  packaging/test/netctl-firewall-test.sh
#   exit 0 = pass (or skipped where userns/nft unavailable), 1 = assertion failed.
set -uo pipefail

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
HELPER="$(cd "$(dirname "$0")/../sbin" && pwd)/wattpost-netctl"

# ---- inner: we are inside the user+net namespace ----
if [ "${1:-}" = "--inner" ]; then
  HELPER="$2"; fail=0
  stub="$(mktemp -d)"
  cat > "$stub/systemctl" <<'STUB'
#!/usr/bin/env bash
# fake systemctl: tracks ssh state in $SSH_MARKER, never touches the host
case "$*" in
  *"enable --now ssh"*)  touch "$SSH_MARKER" ;;
  *"disable --now ssh"*) rm -f "$SSH_MARKER" ;;
  *"is-active --quiet ssh"*) [ -f "$SSH_MARKER" ] ;;
esac
exit $?
STUB
  chmod +x "$stub/systemctl"
  export SSH_MARKER="$stub/ssh.on" PATH="$stub:$PATH"

  # Capture-then-match: `nft ... | grep -q` would trip `pipefail` because
  # grep -q closes the pipe on first match -> nft gets SIGPIPE -> non-zero.
  has()    { local rs; rs="$(nft list ruleset 2>/dev/null)"; grep -qF -- "$1" <<<"$rs"; }
  okhas()  { if has "$1"; then echo "  ok  : has [$1]"; else echo "  FAIL: missing [$1]"; fail=1; fi; }
  oknot()  { if has "$1"; then echo "  FAIL: unexpected [$1]"; fail=1; else echo "  ok  : lacks [$1]"; fi; }
  notrun() { if "$@" >/dev/null 2>&1; then echo "  FAIL: (should have failed) $*"; fail=1; else echo "  ok  : refused [$*]"; fi; }

  echo "[1] apply firewall=on ssh=off"
  "$HELPER" apply on off >/dev/null
  okhas "policy drop"; okhas "tcp dport 80 accept"; okhas "udp dport 5353 accept"
  okhas 'iif "lo" accept'; okhas "ct state established,related accept"; oknot "dport 22"

  echo "[2] apply firewall=on ssh=on -> opens 22"
  "$HELPER" apply on on >/dev/null
  okhas "tcp dport 22 accept"; okhas "tcp dport 80 accept"

  echo "[3] idempotent re-apply (atomic replace: no duplicate / no leak)"
  before="$(nft list ruleset)"; "$HELPER" apply on on >/dev/null; after="$(nft list ruleset)"
  if [ "$before" = "$after" ]; then echo "  ok  : ruleset identical after re-apply"
  else echo "  FAIL: ruleset changed on re-apply"; fail=1; fi

  echo "[4] toggle ssh off again -> 22 recloses (no stale rule survives)"
  "$HELPER" apply on off >/dev/null
  oknot "dport 22"; okhas "tcp dport 80 accept"

  echo "[5] status reflects the live state"
  st="$("$HELPER" status 2>/dev/null)"
  grep -q "firewall=on" <<<"$st" && echo "  ok  : status firewall=on" \
    || { echo "  FAIL: status firewall"; fail=1; }

  echo "[6] apply firewall=off -> table removed"
  "$HELPER" apply off off >/dev/null
  notrun nft list table inet wattpost
  st="$("$HELPER" status 2>/dev/null)"
  grep -q "firewall=off" <<<"$st" && echo "  ok  : status firewall=off" \
    || { echo "  FAIL: status off"; fail=1; }

  rm -rf "$stub"
  echo
  [ "$fail" = 0 ] && echo "RESULT: ALL PASS" || echo "RESULT: FAILURES"
  exit "$fail"
fi

# ---- outer: preconditions, then re-exec inside the namespace ----
command -v nft     >/dev/null 2>&1 || { echo "SKIP: nft not present"; exit 0; }
command -v unshare >/dev/null 2>&1 || { echo "SKIP: unshare not present"; exit 0; }
unshare --user --map-root-user --net true 2>/dev/null \
  || { echo "SKIP: unprivileged user+net namespaces unavailable"; exit 0; }
[ -x "$HELPER" ] || { echo "ERROR: helper not executable at $HELPER"; exit 2; }
echo "wattpost-netctl firewall test — isolated user+net namespace"
exec unshare --user --map-root-user --net "$SELF" --inner "$HELPER"

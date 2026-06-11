# shellcheck shell=bash
# WattPost privileged-artifact sync (#33 + updater parity).
#
# Single source of truth for installing the root-owned, host-level privileged
# surface that lives OUTSIDE the /opt/wattpost slot tree: the privileged
# helper daemon, its socket/service units, the network-control helper, the
# tmpfiles entry, the rollback watchdog, and the admin CLIs.
#
# Sourced by BOTH packaging/install.sh (fresh install / SD-image build) and
# packaging/cli/wattpost-update (atomic slot swap), so the two paths can never
# drift. That drift is exactly the bug this closes: wattpost-update flips the
# slot symlink + rebuilds the venv but historically never refreshed any of the
# host-level helpers, so a release that changed wattpost-helperd shipped a
# stale binary until someone re-ran install.sh by hand — surfacing as
# "unknown action: net_status" from a daemon that out-ran its helper.
#
# Usage (the file is sourced, then the function called):
#     . "<packaging>/lib/wattpost-privileged-sync.sh"
#     wp_sync_privileged "<packaging>"
# where <packaging> is the packaging/ directory of the source being installed
# (install.sh: ${SCRIPT_DIR}; updater: the freshly-swapped slot's src/packaging).
#
# Contract:
#   * Idempotent — safe to run on every install and every update.
#   * Best-effort — every step is guarded so it can run under `set -e` in the
#     updater WITHOUT aborting an already-committed, health-checked slot swap.
#     A failed sync logs a warning; it never rolls back a healthy daemon.
#   * Live-safe — the helper binary is swapped with `try-restart` (re-execs
#     only if the helper is currently up; never force-starts an idle one), and
#     the updater self-update is a rename so a running updater keeps its open
#     inode and finishes from it, with the new version applying next run.
#
# Optional logging: set WP_SYNC_LOG to the name of a logging function in the
# caller's scope (e.g. `log` in wattpost-update, which tees to the update log).
# Defaults to plain echo.

wp_sync_privileged() {
    local pkg="${1:-}"
    local say="${WP_SYNC_LOG:-echo}"

    if [ -z "${pkg}" ] || [ ! -d "${pkg}" ]; then
        "$say" "wp_sync_privileged: packaging dir '${pkg}' missing — skipping privileged sync"
        return 0
    fi

    # --- /usr/local/sbin: root-owned helper binaries ---
    install -d /usr/local/sbin 2>/dev/null || true
    if [ -f "${pkg}/sbin/wattpost-helperd" ]; then
        if install -m 0755 -o root -g root \
            "${pkg}/sbin/wattpost-helperd" /usr/local/sbin/wattpost-helperd; then
            "$say" "synced wattpost-helperd"
        else
            "$say" "WARN: failed to install wattpost-helperd"
        fi
    fi
    if [ -f "${pkg}/sbin/wattpost-netctl" ]; then
        install -m 0755 -o root -g root \
            "${pkg}/sbin/wattpost-netctl" /usr/local/sbin/wattpost-netctl \
            || "$say" "WARN: failed to install wattpost-netctl"
    fi

    # --- tmpfiles drop-in: recreates the /run/wattpost socket dir on boot ---
    if [ -f "${pkg}/tmpfiles.d/wattpost.conf" ]; then
        install -m 0644 -o root -g root \
            "${pkg}/tmpfiles.d/wattpost.conf" /etc/tmpfiles.d/wattpost.conf \
            || "$say" "WARN: failed to install tmpfiles.d/wattpost.conf"
        systemd-tmpfiles --create /etc/tmpfiles.d/wattpost.conf 2>/dev/null || true
    fi

    # --- systemd units: helper socket+service, rollback watchdog, main daemon.
    #     File install only; the helper restart is handled below and the main
    #     daemon restart stays with the caller (install.sh / updater own that,
    #     the updater gates it behind a health probe). ---
    local unit
    for unit in wattpost-helper.socket wattpost-helper.service \
                wattpost-rollback.service wattpost.service; do
        if [ -f "${pkg}/systemd/${unit}" ]; then
            install -m 0644 "${pkg}/systemd/${unit}" "/etc/systemd/system/${unit}" \
                || "$say" "WARN: failed to install unit ${unit}"
        fi
    done

    # --- /usr/local/bin: admin CLIs ---
    if [ -f "${pkg}/cli/wattpost-rollback" ]; then
        install -m 0755 -o root -g root \
            "${pkg}/cli/wattpost-rollback" /usr/local/bin/wattpost-rollback \
            || "$say" "WARN: failed to install wattpost-rollback"
    fi
    if [ -f "${pkg}/cli/wattpost-config" ]; then
        install -m 0755 \
            "${pkg}/cli/wattpost-config" /usr/local/bin/wattpost-config \
            || "$say" "WARN: failed to install wattpost-config"
    fi

    # --- the updater itself ---
    # install(1) opens the destination O_TRUNC and writes in place, which would
    # corrupt THIS running script if an update is what called us. Stage to a
    # temp on the same filesystem and rename: rename(2) is atomic and leaves the
    # running process reading its original (now-unlinked) inode to EOF. The new
    # version takes effect on the next invocation — expected for a self-update.
    if [ -f "${pkg}/cli/wattpost-update" ]; then
        local tmp=/usr/local/bin/.wattpost-update.new
        if cp "${pkg}/cli/wattpost-update" "${tmp}" 2>/dev/null; then
            chmod 0755 "${tmp}" 2>/dev/null || true
            chown root:root "${tmp}" 2>/dev/null || true
            if mv -f "${tmp}" /usr/local/bin/wattpost-update; then
                "$say" "synced wattpost-update (applies next run)"
            else
                rm -f "${tmp}" 2>/dev/null || true
                "$say" "WARN: failed to install wattpost-update"
            fi
        fi
    fi

    # --- reconcile systemd state ---
    systemctl daemon-reload 2>/dev/null || true
    # Socket-activated helper: keep the listener enabled + armed...
    systemctl enable wattpost-helper.socket >/dev/null 2>&1 || true
    systemctl start  wattpost-helper.socket >/dev/null 2>&1 || true
    # ...then swap the running helper binary so the new code goes live.
    #
    # CAREFUL: the updater that sources this lib is (on an older helperd spawn)
    # a child of wattpost-helper.service, so it lives in THAT service's cgroup.
    # A direct `systemctl try-restart wattpost-helper.service` here would
    # cgroup-kill the updater mid-flight — before it runs its own `systemctl
    # restart wattpost.service` — leaving the new code swapped in on disk but
    # the OLD daemon still running (the "stuck on update ready" bug). So DETACH
    # the helper restart onto a short transient timer: the updater finishes its
    # daemon restart + post-swap health gate first (~30s), then the helper
    # re-execs to the new binary at +90s (or sooner via socket activation).
    # try-restart is a no-op when the service is stopped, so an idle helper is
    # left alone. A newer helperd spawns the updater in its own cgroup, which
    # makes this defer belt-and-braces — but it's what lets the very update
    # carrying the fix complete on an old helperd.
    if command -v systemd-run >/dev/null 2>&1; then
        # No --unit (auto-named, --collect reaps it) so a lingering name can't
        # make this fail and fall back to a synchronous, updater-killing restart.
        systemd-run --collect --quiet --on-active=90s \
            systemctl try-restart wattpost-helper.service >/dev/null 2>&1 || true
    else
        # No systemd → no cgroup kill to worry about; restart inline.
        systemctl try-restart wattpost-helper.service >/dev/null 2>&1 || true
    fi

    return 0
}

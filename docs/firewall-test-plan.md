# Firewall cross-mode test plan (Security epic, Phase B)

Validates the nftables inbound firewall across every network mode **on a real
Pi image** (not Docker — the firewall is image-only). This is the last
sign-off item before Phase B is done. Run it once per release that touches
`netsec` / `wattpost-netctl` / the firewall rules.

## Automated portion (no Pi needed)

The helper's ruleset *logic* is covered by an automated test —
`packaging/test/netctl-firewall-test.sh` — which runs `wattpost-netctl` in an
**unprivileged user+net namespace** (no root, no Pi) and asserts: default-deny
policy, the allowed ports, SSH-port gating, **atomic-replace idempotency**
(re-apply doesn't duplicate or leak rules), SSH-off recloses port 22, status
reporting, and teardown. Run it anywhere with `nft` + user namespaces:

```bash
packaging/test/netctl-firewall-test.sh
```

What it **can't** cover — and so still needs a real Pi — is the
hardware/mode behaviour below: **WiFi-client** and **hotspot-AP** modes, and
real **mDNS / `.local`** resolution. Run those manually.

## Setup

- Flash the current image, boot, note the LAN IP (`hostname -I`).
- **Keep the recovery path in your pocket:** from the Pi console,
  `sudo wattpost-config --firewall-off` disables the firewall + restarts if
  you lock yourself out. Re-enable from Settings afterwards.
- You'll need a **second device on the LAN** (laptop/phone) for the
  outside-in checks.

**Handy commands**
```bash
# On the Pi:
sudo nft list table inet wattpost      # ruleset present? policy drop?
systemctl is-active ssh                 # ssh on/off
sudo wattpost-netctl status             # ssh=.. firewall=..

# From the second device (replace <ip>):
ping wattpost.local                     # mDNS / .local resolves?
curl -sS -m5 -o /dev/null -w '%{http_code}\n' http://<ip>/   # dashboard (expect 200/302)
nc -vz -w3 <ip> 22                      # SSH port: open only when SSH on
nc -vz -w3 <ip> 9999                    # random port: always refused/timeout
nmap -Pn <ip>                           # only 80 (+22 if SSH on) should be open
```

## Baseline (re-run in every mode)

1. **Firewall loaded** — `nft list table inet wattpost` shows the chain with `policy drop`.
2. **Dashboard reachable** — `http://<ip>/` returns 200/302 from the second device.
3. **`.local` resolves** — `ping wattpost.local` / `http://wattpost.local` works (mDNS 5353 allowed).
4. **Closed ports dropped** — random high port times out; `nmap` shows only the intended ports.
5. **Outbound still works** — dashboard data keeps updating (OUTPUT is deliberately open).

## Modes

### 1. Wired Ethernet (default)
Run the baseline. Expect: 80 + mDNS open, 22 closed, dashboard + `.local` fine.

### 2. WiFi client (joined to your router)
Disconnect Ethernet, join WiFi, re-run the baseline on the WiFi IP. Confirm `.local` still resolves over WiFi.

### 3. Hotspot / AP mode  ← riskiest, test carefully
Put the appliance into hotspot mode; join its AP from a phone. Verify:
- The AP **hands out DHCP** (you get an IP) — udp 67/68 allowed.
- The AP's **DNS (53)** resolves — captive/local lookups work.
- Dashboard reachable at the AP gateway IP on **80**.
- `.local`/mDNS works on the AP.
The helper allows 53/67 unconditionally precisely so AP mode works without
brittle interface-name matching — confirm that's actually the case here.

### 4. SSH ON
Toggle SSH on (Settings, or `web.ssh_enabled: true` + restart). Verify:
- `systemctl is-active ssh` → `active`.
- `nc -vz <ip> 22` **succeeds** from the second device; you can `ssh` in.
- `nft ...` shows the `tcp dport 22 accept` rule.

### 5. SSH OFF
Toggle SSH off. Verify:
- sshd stopped; port 22 **refused/timed out** from outside.
- the `tcp dport 22` rule is gone from `nft`.
- (an active SSH session drops — expected.)

### 6. Reboot persistence
Reboot the Pi. After it's up, re-run the baseline **without touching anything** — the firewall must be **re-applied automatically** (daemon boot reconcile), allow/deny intact, SSH state matching config. Confirms we don't rely on nft persistence.

### 7. Recovery path
With SSH off, prove you can still recover from the console:
`sudo wattpost-config --firewall-off` → dashboard reachable again, and
`nft list table inet wattpost` now errors (table gone). Re-enable from Settings.

## Sign-off

All modes: dashboard + `.local` reachable, only intended ports open, outbound
fine, firewall survives reboot, recovery works → tick the Phase B test box on
ritualnorth/wattpost-cloud#15.

# How a laptop on the same network made our Renogy BT-2 dongle invisible

We lost most of a day to a Renogy BT-2 dongle that refused to
advertise. Two appliances on the same LAN. Both trying to talk to
the same dongle. Only one can win, and we did not understand who.
This is the debugging story and the wizard hint we shipped so the
next person does not have to repeat it.

## The symptom

A Raspberry Pi appliance at `192.168.1.100`, freshly flashed, sat
on the dashboard showing all zeros. The setup wizard scanned for
Bluetooth devices and came back empty. No BT-2, no Victron, no JK
BMS. Nothing.

The dongle was a Renogy BT-2 plugged into a Renogy Rover charge
controller two metres from the Pi. The blue LED on the dongle was
solid, meaning paired and active. The Renogy DC Home app on a
phone in the room could see and read from the dongle perfectly.

Two readings of the wizard's scan completed back-to-back with no
results. The third found two random Bluetooth devices we did not
recognise (a smart bulb, a fitness watch) but no BT-2.

That should have been the tell. We will get to why.

## What we tried first

We assumed the problem was on the Pi. So:

1. **Restart the WattPost daemon** in case the BLE adapter was
   in a bad state. No change.
2. **`bluetoothctl scan on`** at the system level to take WattPost
   out of the loop. Same result: the BT-2 was not in the device
   list.
3. **Power-cycle the BT-2** by unplugging and replugging the RJ45
   cable. The blue LED came back solid. Still invisible.
4. **Move the dongle closer**. Centimetres of distance. Still
   invisible.
5. **Swap dongles** with a known-good BT-2 from another rig. Same
   problem with the second dongle.

Five failed theories in. Two hours gone.

## The first wrong fix

At this point we suspected the Pi's Bluetooth stack. Realtek
chips paired with BlueZ 5.72 have a documented case of silent scan
failure when an HCI command sequence races
([cyril/renogy-bt#97](https://github.com/cyril/renogy-bt/issues/97)
documents this for the Renogy library specifically).

So we wrote a workaround: a process-wide lock around BLE discovery
that pauses Victron's passive scanner before starting any new
scan, then resumes it after. It made the wizard scan more
reliable in general (we kept the lock), but the BT-2 stayed
invisible. The lock was real but not the cause.

Three hours in.

## The signal we ignored

Around hour four, we noticed the phone app's behaviour properly.
DC Home on the phone could read the BT-2 fine. So could
VictronConnect on a different phone reading the SmartShunt also
in the room. The only thing that could not see the BT-2 was the
Pi.

The Renogy BT-2 is a single-master BLE device. Once one BLE
central holds an active connection to it, it stops advertising
entirely. It does not multiplex. It does not queue. It vanishes
from every other scanner on the radio.

This is documented in cyril/renogy-bt and in two paragraphs
buried in Renogy's RS232 spec PDF. We knew this. We had even
written a hint into the wizard for it: when the scan finds a BT-2
once and then loses it on a subsequent scan, the wizard surfaces
"Renogy BT-2 only allows one connection at a time. Force-quit the
DC Home / DC Connect app on any phone in range, or power-cycle
the dongle."

The hint did not fire because we had **never** seen the BT-2 in
this session. The detection logic compares scans against a recent
cache. With an empty cache, there is nothing to compare.

The phone app was not the holder. We confirmed by closing every
app on every phone in the building, then waiting two minutes for
any stale BLE connection to time out. Still invisible.

## The actual cause

We had a laptop at `192.168.1.13` on the same LAN. Earlier in
the week, while testing the appliance Docker image, we ran a
WattPost container on that laptop and pointed it at the same
BT-2 dongle. The container was still running. Its config still
named the same dongle. It was still polling, several times a
minute, holding the BLE master slot.

Two appliances. One BT-2. The laptop was winning, by virtue of
having been started first, and its connection was healthy enough
that the dongle stayed bonded to it. Every reconnect attempt
from the Pi failed silently because there was no advertisement
to chase.

The fix was three words: `docker stop wattpost`. On the laptop.
The dongle's blue LED flickered for half a second. The Pi's next
scan found it. The dashboard filled in within ten seconds.

Six hours.

## The wizard hint we shipped

The signal that would have shortened this debugging session to
minutes was right there in the network all along. Another
WattPost was running on `192.168.1.13`. The dongle exclusivity
was real but the holder was not on Bluetooth, it was on TCP.

We added a LAN peer scan to the wizard. When a BLE scan
completes with zero Renogy devices found, the appliance probes
the local subnet for other WattPost instances. Each candidate IP
gets a fast TCP connect followed by a `GET /api/health`. If the
response contains `service: "wattpost"`, that IP is a peer.
Peers show up above the empty scan result with a yellow panel
explaining the single-master rule and what to do.

The implementation is in
[`solar_monitor/api/setup.py`](https://github.com/ritualnorth/wattpost/blob/main/solar_monitor/api/setup.py).
The whole helper, including the docker-network-aware anchor
selection, fits in 80 lines. The discriminator is small enough
to be useful and unique enough to avoid false positives. We are
not going to flag every web server on the LAN; only the ones
answering with the right service string get listed.

> Note: the route-IP anchoring took two passes. The first version
> used `gethostname()` to find the local IP, which in a host-mode
> Docker container returns the `172.17.0.1` docker0 bridge IP
> alongside the real LAN address. The scan ran against the
> docker subnet, found nothing, and reported zero peers. The
> kernel-route trick (open a UDP socket to a public IP, read back
> the local endpoint) gives us the actual outbound IP and the
> right /24.

## What we learned

A symptom that looks like a hardware fault is often a coordination
fault. The Pi's Bluetooth stack was fine. The dongle was fine.
The radio was fine. The conflict was at the protocol layer, two
levels of indirection away from where the error showed up.

The detection mechanism we already had (cache-based "where did
this MAC go?") only fires on the second scan. For fresh installs
that have **never** seen the device, the cache cannot help. We
needed a second axis: the network around us, not just the radio.

If you hit this with another vendor's dongle: the same
single-master pattern applies to most consumer BLE bridges. The
specific behaviour, whether the dongle stops advertising entirely
or keeps advertising but rejects new connections, varies by
manufacturer. The diagnostic step is identical. Check whether
another host on your network is talking to it first.

## Conclusion

The wizard hint ships in the v0.1.20 appliance image. If you are
running an earlier version and hit silent BT-2 syndrome, the
manual check is one ARP-scan plus a curl:

```bash
# Find other hosts on your subnet:
ip neigh | awk '/REACHABLE/ {print $1}'

# Probe each one for a WattPost instance:
for ip in $(ip neigh | awk '/REACHABLE/ {print $1}'); do
  curl -s --max-time 1 "http://$ip:8000/api/health" \
    | grep -q '"service":"wattpost"' && echo "wattpost peer: $ip"
done
```

If a peer turns up, stop its WattPost daemon (or remove the
shared dongle from its config), then scan again from the
appliance you actually want to keep.

For setup-flow context, see
[the install guide](/blog/first-install). For the full wizard
docs, see [Wired setup](/docs/wired-setup), which covers the
USB-RS485 alternative that side-steps the entire BLE-exclusivity
question.

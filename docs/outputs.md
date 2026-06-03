# Controllable outputs

Most of WattPost reads. Outputs is the part that **writes** — turning a
load relay or a smart plug on and off, and (optionally) doing it
automatically when there's spare solar. Everything here is opt-in and
guarded; nothing toggles until you've passed a safety confirmation.

## What you can control

- **Renogy Rover load output.** Rover-family MPPTs (and the Wanderer /
  Adventurer / Voyager siblings) expose a 12 V load terminal. WattPost
  toggles it over Modbus and reads the state back to confirm the flip
  landed. Bigger Rovers without an `L` terminal simply don't show a load
  panel.
- **Smart plugs over local HTTP.** Two protocols, both LAN-only, no
  broker, no cloud, no Home Assistant in the loop:
  - **Shelly Gen2** (Plug S, Plus, Pro) via local JSON-RPC.
  - **Tasmota** (any Sonoff / Athom / similar flashed with Tasmota) via
    its `/cm?cmnd=Power` surface.
- **Writable charge settings.** Charger profile, absorption / float
  voltages, low-voltage cutoffs and the like are a separate, related
  surface — see [Writable settings](/docs/writable-settings).

## Adding a smart plug

Smart plugs are configured in `config.yaml` (a wizard reads the same
list):

```yaml
smart_plugs:
  - name: Fridge plug
    kind: shelly_gen2        # "shelly_gen2" | "tasmota"
    host: 192.168.1.50       # LAN address or hostname
    user: ""                 # optional device login
    password: ""             # optional device password
```

Each entry becomes one controllable output and shows up in the same
list as the Renogy load relay — including the solar-pause dropdown.

## Schedules

Any output can carry **time-based schedules** — "turn the plug on at
sunrise, off at 22:00" — built from a trigger (`time`, `sunrise`, or
`sunset`, with a ± minute offset for the solar triggers) and a 7-bit
day-of-week mask. Manage them per output.

## Solar-aware charger pause

The **solar pause** controller can automatically pause an AC charger
when the bank is full and solar is covering the load, and bring it back
when the battery drops — saving grid / generator runtime without you
watching the dashboard. It evaluates the bank, PV, and charger state on
every poll cycle against four gates:

- **Hard floor** — below `hard_floor_soc`, force the charger ON
  regardless of the forecast. The floor always wins.
- **Recover** — below `recover_soc` while paused, switch the charger
  back ON.
- **Pause** — above `target_soc`, bank net-positive, and PV producing at
  least `pv_surplus_w`, pause the charger.
- **Cooldown** — `cooldown_minutes` between any two changes so it
  doesn't flap on a passing cloud.

Thresholds must satisfy `hard_floor < recover < target` with a 10-point
gap each side; a manual toggle always overrides the controller until the
next auto-change.

```yaml
solar_pause:
  enabled: true
  charger_output_id: fridge_plug      # an output id from /api/outputs
  target_soc: 80
  recover_soc: 50
  hard_floor_soc: 30
  pv_surplus_w: 50
  cooldown_minutes: 30
```

## API

All endpoints sit behind the same local auth as the rest of the
dashboard.

| Method & path | Purpose |
| --- | --- |
| `GET    /api/outputs` | List every output (`?device=<label>` to filter) |
| `POST   /api/outputs/<id>/confirm` | Pass the one-time safety gate for an output |
| `POST   /api/outputs/<id>/toggle` | Flip state (`{ "on": true }`); `409` until confirmed |
| `GET    /api/outputs/<id>/schedules` | List schedules for an output |
| `POST   /api/outputs/<id>/schedules` | Create a schedule |
| `PUT    /api/outputs/<id>/schedules/<sid>` | Edit a schedule |
| `DELETE /api/outputs/<id>/schedules/<sid>` | Remove a schedule |
| `GET    /api/outputs/solar_pause` | Read solar-pause settings + live status |
| `PUT    /api/outputs/solar_pause` | Update + hot-apply solar-pause settings |

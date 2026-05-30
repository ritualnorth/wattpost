# Recommended hardware

WattPost runs on a Raspberry Pi. The software side is the same across
every form factor; this page lists case + screen combos that have
been tested or that we'd reach for if we were building a new install.

The "right" combo depends on **what mode the install is for** — the
permanently-on cabin display has very different needs from the
glance-and-go van dashboard. Modes are central to the picker on the
cloud dashboard (`/app/site/<id>`) and to the kiosk routes below.

> **Choosing a mode**
>
> | Use case | Mode | What to optimise for |
> |---|---|---|
> | Off-grid cabin permanently watching a battery bank | **Cabin** | low power, sun-readable, large numbers |
> | Mobile van/RV install with a touch dashboard | **Van** | colour, touch, quick switch between screens |
> | Marine/boat install | **Marine** | sunlight readability, anchor watch |
> | Home install with a fridge-magnet tablet | **Kiosk** | cheap, big enough, no UX |
> | Single-user home laptop / phone | **Home** | no extra hardware needed |

## Pin a screen at a mode

The appliance exposes mode-aware kiosk URLs:

```
http://<pi-ip>/kiosk           — default (no mode badge)
http://<pi-ip>/kiosk/cabin     — Cabin mode (green accent + badge)
http://<pi-ip>/kiosk/van       — Van mode (amber accent + badge)
http://<pi-ip>/kiosk/marine    — Marine mode (deep-blue accent + badge)
http://<pi-ip>/kiosk/home      — Home mode (default brand accent)
http://<pi-ip>/kiosk/kiosk     — explicit Kiosk mode (neutral grey)
```

These routes are anonymous-allowed (no login prompt) and bookmarkable.
Drop one of them into chromium-kiosk on the Pi and the wall display
shows the mode-tinted SoC + flow with a small badge in the top
corner.

## Tested combos

### Cabin — Waveshare 7.5" e-ink (low-power, "always on")

```
Pi Zero 2 W   £15
Waveshare 7.5" V2 e-ink HAT   £55
Pi Zero 2 W case (any)   £6
USB-C 5 V 2.5 A supply   £8
microSD 32 GB    £8
                          ────
                           £92
```

- Average power draw: ~0.1 W (refreshes every 60 s; sleeps between).
- Excellent in direct sunlight.
- Black-and-white only; the kiosk view degrades gracefully but you lose the mode-accent colour.
- Best for: permanently-on cabin display you check once a day.

### Van — Argon ONE M.2 + 3.5" DSI touchscreen

```
Pi 4 (4 GB)               £55
Argon ONE M.2 case        £55
Waveshare 3.5" DSI touch  £42
microSD 32 GB             £8
                          ────
                          £160
```

- Colour, touch, full kiosk-chromium UX.
- ~2 W steady — fine on a van DC-DC.
- Touch lets the driver swipe between SoC and the energy view.
- Best for: mobile installs where someone'll actually interact with the screen.

### Marine — Waveshare 4.3" HDMI + glare-killer

```
Pi 4 (4 GB)               £55
Waveshare 4.3" HDMI       £55
Any sealed Pi case        £15
9-30 V → 5 V DC-DC        £15
microSD 32 GB             £8
                          ────
                          £148
```

- HDMI gives more flexibility on glare-killer add-ons / matte films.
- DC-DC step-down lets you feed it straight off the house bank.
- ~3 W — bigger backlight.
- Best for: salty environments where you'll mount it behind a sealed window.

### Home / Kiosk — recycled tablet

The cheapest path. **Don't buy a Pi screen at all** — point a spare
iPad or Android tablet at the kiosk URL on the LAN. Wake Lock keeps
the screen on; the kiosk view is touch-friendly.

```
Spare tablet                       £0
microUSB / USB-C charger nearby    £0
                                   ────
                                    £0
```

- Best for: anyone already with a tablet kicking around.
- Caveat: needs the appliance reachable on the same LAN (or via the
  cloud share link `/k/<token>`).

## Power budget cheat-sheet

Off-grid installs care about this — a 2 W screen eats 48 Wh/day, or
about 4 Ah at 12 V. Worth knowing before you commit.

| Combo | Power (steady) | Per day at 12 V |
|---|---|---|
| Pi Zero + e-ink (Cabin) | ~0.1 W | ~0.2 Ah |
| Pi 4 + 3.5" DSI (Van) | ~2 W | ~4 Ah |
| Pi 4 + 4.3" HDMI (Marine) | ~3 W | ~6 Ah |
| Recycled tablet | ~2-5 W | ~4-10 Ah |

## What about the official Pi touchscreens?

The 7" Pi-official touchscreen works fine but it's expensive (~£75)
and the build quality of the cables is mixed. Waveshare's equivalents
are cheaper and easier to mount via standard DSI ribbon. Either works
with our kiosk routes.

## What we're NOT recommending

- **Tiny SPI OLED + custom UI.** Looks slick but needs a native
  Python UI rather than the chromium-kiosk SPA. Not impossible, but
  scope-creep for v1.
- **HDMI projector kiosks.** People have asked; the chromium-kiosk
  works fine on a projector input, but mounting and 24/7 reliability
  are projector-side problems we can't help with.

## Future — branded appliance

If a "WattPost Van" or "WattPost Cabin" SKU ships as a finished
appliance (Pi + case + screen pre-flashed) it'll come from the
combos above. See the `hardware-product-line` memory for where that
decision sits.

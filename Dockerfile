# WattPost appliance — container image.
#
# Built primarily for demo.wattpost.io: spawns the daemon with
# WATTPOST_DEMO=1 so it produces synthetic data and 403s writes.
# Same image works for any "appliance, but without real BLE" use
# (CI smoke tests, local dev without a real Pi, etc).
#
# NOT what we ship to customers — customers get the SD card image
# produced by .github/workflows/build-image.yml.
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/ritualnorth/wattpost"
LABEL org.opencontainers.image.description="WattPost appliance daemon — for demo + CI use"

# Slim deps: bluez/dbus libraries that bleak would normally need at
# runtime are skipped because demo mode never touches BLE. Keep the
# layer set tiny so the image stays under ~150 MB.
RUN apt-get -y update \
 && apt-get -y install --no-install-recommends \
        ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source FIRST then pip install — setuptools' find-packages
# resolves at install time, so if solar_monitor/ isn't on disk yet
# `pip install .` installs the entry-point script but no actual
# package, and the script crashes on import. (Bit us once already.)
COPY pyproject.toml /app/
COPY solar_monitor /app/solar_monitor
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefer-binary .

ENV PYTHONUNBUFFERED=1 \
    WATTPOST_DEMO=1 \
    WATTPOST_CONFIG=/app/demo-config.yaml \
    WATTPOST_DB=/var/lib/wattpost/solar-monitor.db

# Demo config. The synthetic poller doesn't read transports/devices,
# but the rest of the daemon's config loader expects something
# parseable. Weather + forecast wired so the dashboard's weather tile
# and Solcast forecast strip are populated — the synthetic forecast
# provider needs no API key and never hits the network. Weather uses
# real Open-Meteo (free, no key) seeded with a London coordinate
# (visitors get an actual-looking forecast for "somewhere"). Coords
# can be tweaked if we ever theme the demo around a different site.
RUN mkdir -p /var/lib/wattpost && \
    printf '%s\n' \
      'transports: []' \
      'devices: []' \
      'exporters: []' \
      'notification_transports: []' \
      'alerts: []' \
      'forecast:' \
      '  provider: synthetic' \
      '  poll_hours: 24' \
      'weather:' \
      '  provider: openmeteo' \
      '  lat: 51.5074' \
      '  lon: -0.1278' \
      '  poll_minutes: 15' \
        > /app/demo-config.yaml

EXPOSE 8000

# Use the installed `solar-monitor` console script (pyproject.toml's
# [project.scripts] → solar_monitor.cli:main). The package itself
# has no __main__.py, so `python -m solar_monitor` would error.
CMD ["solar-monitor", "serve", \
     "--config", "/app/demo-config.yaml", \
     "--db", "/var/lib/wattpost/solar-monitor.db", \
     "--interval", "60", \
     "--port", "8000", \
     "--host", "0.0.0.0"]

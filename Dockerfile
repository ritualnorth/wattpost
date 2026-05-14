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

LABEL org.opencontainers.image.source="https://github.com/ritualnorth/offgrid-monitor"
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

# Minimal config — synthetic poller doesn't actually read the
# transports/devices list, but the daemon's config loader expects
# something parseable. Forecast + weather + cloud all off in demo.
RUN mkdir -p /var/lib/wattpost && \
    printf 'transports: []\ndevices: []\nexporters: []\nnotification_transports: []\nalerts: []\n' \
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

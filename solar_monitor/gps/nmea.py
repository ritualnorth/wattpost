"""Minimal NMEA 0183 sentence decoder.

We only care about the RMC sentence (Recommended Minimum
navigation Course), it carries time + lat/lon + status in one
line, emitted ~1Hz by every GPS receiver. Other sentences (GGA,
GSV, GSA) carry extra detail we don't need for "where am I right
now."

Receiver vendors prefix the sentence with their constellation:
  $GPRMC  GPS only
  $GLRMC  GLONASS
  $GARMC  Galileo
  $GNRMC  multi-constellation (most modern receivers)

We accept any of those. Checksum validation skipped, RMC frames
are short, the underlying USB-CDC link is reliable, and a corrupt
frame just fails our coordinate parse and gets ignored.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_RMC_PREFIXES = ("$GPRMC", "$GLRMC", "$GARMC", "$GNRMC", "$BDRMC")


def _parse_lat(value: str, hemisphere: str) -> float | None:
    """Convert NMEA `DDMM.MMMM` + N/S into signed decimal degrees."""
    if not value or len(value) < 4:
        return None
    try:
        # Latitude: 2-digit degrees + decimal minutes.
        deg = int(value[:2])
        minutes = float(value[2:])
        result = deg + minutes / 60.0
        if hemisphere == "S":
            result = -result
        return result
    except ValueError:
        return None


def _parse_lon(value: str, hemisphere: str) -> float | None:
    """Convert NMEA `DDDMM.MMMM` + E/W into signed decimal degrees."""
    if not value or len(value) < 5:
        return None
    try:
        # Longitude: 3-digit degrees + decimal minutes.
        deg = int(value[:3])
        minutes = float(value[3:])
        result = deg + minutes / 60.0
        if hemisphere == "W":
            result = -result
        return result
    except ValueError:
        return None


def _parse_rmc_time(time_str: str, date_str: str) -> int | None:
    """RMC carries UTC time as HHMMSS(.sss) and date as DDMMYY.
    Returns unix-second timestamp, or None when either field is
    missing/malformed."""
    if not time_str or not date_str or len(time_str) < 6 or len(date_str) != 6:
        return None
    try:
        hh = int(time_str[0:2])
        mm = int(time_str[2:4])
        ss = int(float(time_str[4:]))
        dd = int(date_str[0:2])
        mo = int(date_str[2:4])
        yy = 2000 + int(date_str[4:6])
        dt = datetime(yy, mo, dd, hh, mm, ss, tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


_GSV_PREFIXES = ("$GPGSV", "$GLGSV", "$GAGSV", "$GNGSV",
                 "$BDGSV", "$GBGSV", "$QZGSV")


def parse_gsv(line: str) -> dict | None:
    """Decode one NMEA GSV (Satellites in View) sentence.

    GSV is what tells us "the receiver can hear N satellites and how
    strongly", which is exactly the signal a user needs while a cold
    GPS is acquiring a fix — RMC only flips from void→active once a
    full position is computed, so without GSV the UI can't tell
    "no GPS" from "GPS present, still locking".

    Returns `{talker, msg_num, total_in_view, snrs}`:
      * talker        — constellation tag ("GP", "GL", "GA", …); a
                        multi-constellation receiver emits one GSV
                        stream per talker, each with its own total.
      * msg_num       — 1-based sentence index within the burst; a
                        burst of N sats spans ceil(N/4) sentences.
                        Callers reset their per-talker SNR accumulator
                        when msg_num == 1.
      * total_in_view — satellites in view for this talker.
      * snrs          — SNRs (dB-Hz) PRESENT in this sentence (blank
                        SNR fields, i.e. detected-but-not-tracked
                        satellites, are omitted).

    Returns None for non-GSV / malformed lines. Checksum skipped for
    the same reason parse_rmc skips it.

    GSV layout:
      0: $xxGSV
      1: total sentences in this burst
      2: sentence number (1-based)
      3: total satellites in view
      4…: repeating [PRN, elevation, azimuth, SNR] × up to 4
    """
    if not line:
        return None
    line = line.strip()
    if "*" in line:
        line = line.rsplit("*", 1)[0]
    parts = line.split(",")
    if not parts or parts[0] not in _GSV_PREFIXES or len(parts) < 4:
        return None
    talker = parts[0][1:3]
    try:
        msg_num = int(parts[2]) if parts[2] else 1
    except ValueError:
        msg_num = 1
    try:
        total_in_view = int(parts[3]) if parts[3] else 0
    except ValueError:
        total_in_view = 0
    snrs: list[int] = []
    # Satellite blocks begin at index 4, 4 fields each; SNR is the
    # 4th field of each block → absolute indices 7, 11, 15, 19.
    for i in range(7, len(parts), 4):
        v = parts[i]
        if v:
            try:
                snrs.append(int(v))
            except ValueError:
                pass
    return {
        "talker":        talker,
        "msg_num":       msg_num,
        "total_in_view": total_in_view,
        "snrs":          snrs,
    }


def parse_rmc(line: str) -> dict | None:
    """Decode one NMEA RMC sentence into a fix dict, or return None
    if the line isn't an RMC sentence / fix is void / coordinates
    won't parse.

    Returns `{lat, lon, ts_utc, speed_knots, course_deg}` on success.
    `ts_utc` is the UTC timestamp from the GPS, NOT the host clock,
    receivers emit accurate time from the satellites' atomic clocks,
    which is more trustworthy than the Pi's local clock on a fresh
    boot (no NTP yet).
    """
    if not line:
        return None
    # Strip any trailing checksum (`...,*4A`) plus whitespace.
    line = line.strip()
    if "*" in line:
        line = line.rsplit("*", 1)[0]
    parts = line.split(",")
    if not parts or parts[0] not in _RMC_PREFIXES:
        return None
    # RMC layout:
    #   0: $..RMC
    #   1: UTC time (HHMMSS.sss)
    #   2: status (A=active, V=void)
    #   3: latitude (DDMM.MMMM)
    #   4: N/S
    #   5: longitude (DDDMM.MMMM)
    #   6: E/W
    #   7: speed over ground (knots)
    #   8: course over ground (deg)
    #   9: date (DDMMYY)
    if len(parts) < 10:
        return None
    if parts[2] != "A":
        return None  # void fix, no satellite lock yet
    lat = _parse_lat(parts[3], parts[4])
    lon = _parse_lon(parts[5], parts[6])
    if lat is None or lon is None:
        return None
    ts = _parse_rmc_time(parts[1], parts[9])
    try:
        speed = float(parts[7]) if parts[7] else 0.0
    except ValueError:
        speed = 0.0
    try:
        course = float(parts[8]) if parts[8] else 0.0
    except ValueError:
        course = 0.0
    return {
        "lat":          round(lat, 6),
        "lon":          round(lon, 6),
        "ts_utc":       ts,
        "speed_knots":  speed,
        "course_deg":   course,
    }

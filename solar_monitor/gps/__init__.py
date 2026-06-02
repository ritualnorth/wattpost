"""USB GPS support, NMEA-over-serial location updates.

Lets the daemon's "current location" track a moving van/RV. Once
configured, fresh fixes from a `/dev/ttyACM0`-style GPS receiver
update the daemon's effective lat/lon in memory; the weather +
PV-forecast services pick up the new coordinates on their next
fetch.

Designed around the £8 VK-162 G-Mouse + similar puck/stick
receivers: open the port, read NMEA at 9600 baud, decode RMC
sentences for lat/lon. No `gpsd` dependency, pyserial is enough
for a single device.
"""
from .service import GpsService  # noqa: F401
from .nmea import parse_rmc       # noqa: F401

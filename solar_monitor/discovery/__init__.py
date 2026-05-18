"""Anonymous hardware-discovery telemetry (#129).

When opted in (config: discovery.enabled = true), the appliance
forwards fingerprints of unknown devices its scans see to the cloud,
feeding the next-driver pipeline. Everything that leaves the
appliance is reduced to the bare minimum needed to identify a class
of device: MAC vendor prefix, advertised local name, manufacturer-
data id + leading bytes, service UUIDs.

Nothing personally identifying — no full MAC, no serials, no IP, no
appliance id, no email. Cloud rolls observations up by fingerprint
hash, not by source appliance.
"""
from .uploader import build_ble_fingerprint, push_observations  # noqa: F401

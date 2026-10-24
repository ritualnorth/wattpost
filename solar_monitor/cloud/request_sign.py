"""ed25519 request signing for calls from the appliance to the cloud.

Scheme "v2" signs, with the appliance keypair:

    b"v2\\n" + ts + b"\\n" + METHOD + b"\\n" + PATH_WITH_QUERY + b"\\n" + body

so a signed body captured for one endpoint can't be replayed at another (v1
signed only `ts` and `body`). PATH_WITH_QUERY is the request target exactly as
the cloud's server sees it: the URL path plus `?query` when present, as sent on
the wire. METHOD is upper-case. `ts` is the ISO-8601 UTC timestamp sent in
`X-WP-Heartbeat-Ts`, which the cloud checks for freshness (5 minutes).

Headers: X-WP-Heartbeat-Ts / -Fp / -Sig (names kept from v1) plus
`X-WP-Sig-Version: 2`. The same bytes are built by the cloud in
wattpost_cloud/appliance_sig.py; tests/test_request_sign.py and the cloud's
tests/test_appliance_sig_v2.py pin one golden vector. Change both together.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

SIG_VERSION = "2"


def signing_input(ts: str, method: str, target: str, body: bytes) -> bytes:
    return (b"v2\n" + ts.encode("ascii") + b"\n" + method.upper().encode("ascii")
            + b"\n" + target.encode("ascii") + b"\n" + body)


def request_target(url: str | httpx.URL) -> str:
    """Path plus `?query` of `url`, percent-encoded as httpx puts it on the
    wire (what the cloud's server reads as the raw request target)."""
    return httpx.URL(url).raw_path.decode("ascii")


def signed_headers(method: str, url: str | httpx.URL, body: bytes = b"") -> dict[str, str]:
    """Signature headers for one request to `url` carrying exactly `body`.

    Best effort: a keypair load failure returns no headers, and the cloud then
    refuses the request as unsigned. Callers must send these exact `body` bytes
    (pre-serialise JSON and pass `content=`, never `json=`)."""
    try:
        from ..auth import keypair as _kp
        kp = _kp.load_or_create()
        ts = datetime.now(timezone.utc).isoformat()
        sig = kp.sign(signing_input(ts, method, request_target(url), body))
        return {
            "X-WP-Heartbeat-Ts":  ts,
            "X-WP-Heartbeat-Fp":  kp.fingerprint,
            "X-WP-Heartbeat-Sig": base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii"),
            "X-WP-Sig-Version":   SIG_VERSION,
        }
    except Exception:
        log.exception("request signing skipped")
        return {}

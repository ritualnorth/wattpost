"""URL checks for values that come from, or point at, the cloud.

The cloud's identity reply hands the appliance the URLs it will later trust
to verify signed commands (the JWKS) and to bounce a browser to (OIDC
discovery / redirect). Those must not be able to point anywhere but the cloud
the user paired with, and must not be fetched over cleartext.
"""
from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def host_of(url: str) -> str | None:
    """Lower-cased hostname of an absolute http(s) URL, else None
    (no host, userinfo present, unparsable)."""
    try:
        u = urlsplit(str(url).strip())
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            return None
        u.port   # raises ValueError on a junk port
        return u.hostname.lower()
    except ValueError:
        return None


def is_loopback_host(host: str | None) -> bool:
    return bool(host) and host.lower() in LOOPBACK_HOSTS


def scheme_ok(url: str) -> bool:
    """https always; http only for a loopback host (development)."""
    try:
        scheme = urlsplit(str(url).strip()).scheme
    except ValueError:
        return False
    host = host_of(url)
    return host is not None and (scheme == "https" or (scheme == "http" and is_loopback_host(host)))


def same_cloud_host(url: str, endpoint: str) -> bool:
    """`url` is acceptable as cloud-supplied configuration: right scheme and
    the same host as the configured cloud endpoint."""
    h, e = host_of(url), host_of(endpoint)
    return h is not None and e is not None and scheme_ok(url) and h == e


def cloud_or_subdomain(url: str, endpoint: str) -> bool:
    """`url` is https (http for loopback) and on the cloud endpoint's host or a
    subdomain of it. For the per-appliance callback URL the cloud hands out
    (`<slug>.wattpost.cloud`), which is not a trust anchor."""
    h, e = host_of(url), host_of(endpoint)
    if h is None or e is None or not scheme_ok(url):
        return False
    return h == e or (not is_loopback_host(e) and h.endswith("." + e))


def endpoint_problem(endpoint: str) -> str | None:
    """Why `endpoint` is not an acceptable cloud endpoint, or None."""
    host = host_of(endpoint)
    if host is None:
        return "endpoint must be an http(s) URL with a host"
    if urlsplit(endpoint.strip()).scheme == "http" and not is_loopback_host(host):
        return "endpoint must use https (http is only allowed for localhost)"
    return None

"""Captive-portal probe responders (Pillar 3b).

When the hotspot's captive portal is active (AP up + captive_portal
enabled + DNS hijacked so every name resolves to the appliance), a
joining device's OS fires a "connectivity check" at a well-known URL.
With DNS pointing every name at us those requests land here, and
returning anything other than the vendor's expected "you're online"
answer makes the OS pop its "Sign in to network" sheet — which we point
at the dashboard.

When captive is NOT active these routes return the normal "online"
answers, so they're inert if reached any other way.

Every path here is listed in web_auth.ANONYMOUS_PATH_PREFIXES — a joining
client is pre-auth by definition.
"""
from __future__ import annotations

from litestar import Response, get
from litestar.datastructures import State

from ..hotspot.service import AP_GATEWAY

_PORTAL_URL = f"http://{AP_GATEWAY}/"
# What an online OS expects from each vendor check (returned when captive
# is inactive). Apple wants exactly this body; Windows wants these exact
# strings; Android just wants a 204.
_APPLE_SUCCESS = "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"


def _active(state: State) -> bool:
    svc = state["scheduler"].hotspot
    return bool(svc and svc.captive_active)


def _portal() -> Response:
    """302 to the dashboard. Built explicitly (not litestar.Redirect) so
    the absolute cross-host Location is sent verbatim."""
    return Response(content=b"", status_code=302, headers={"Location": _PORTAL_URL})


@get(["/generate_204", "/gen_204"])
async def android_204(state: State) -> Response:
    """Android / ChromeOS. Online → 204 No Content; captive → redirect."""
    if _active(state):
        return _portal()
    return Response(content=b"", status_code=204)


@get(["/hotspot-detect.html", "/library/test/success.html"])
async def apple_detect(state: State) -> Response:
    """iOS / macOS. Online → the literal 'Success' page; captive → redirect."""
    if _active(state):
        return _portal()
    return Response(content=_APPLE_SUCCESS, media_type="text/html")


@get("/connecttest.txt")
async def win_connecttest(state: State) -> Response:
    """Windows 10/11. Online → 'Microsoft Connect Test'; captive → redirect."""
    if _active(state):
        return _portal()
    return Response(content="Microsoft Connect Test", media_type="text/plain")


@get("/ncsi.txt")
async def win_ncsi(state: State) -> Response:
    """Windows (legacy NCSI). Online → 'Microsoft NCSI'; captive → redirect."""
    if _active(state):
        return _portal()
    return Response(content="Microsoft NCSI", media_type="text/plain")


@get("/canonical.html")
async def ubuntu_canonical(state: State) -> Response:
    """Ubuntu / GNOME NetworkManager. Online → empty page; captive → redirect."""
    if _active(state):
        return _portal()
    return Response(content="<html><body></body></html>", media_type="text/html")

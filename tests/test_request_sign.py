"""Request signing v2: the signed bytes bind the HTTP method and the request
target (path + query), so a body signed for one endpoint is useless at another.

The cloud side lives in wattpost-cloud/cloud/wattpost_cloud/appliance_sig.py
and must stay byte-identical; the same golden vector is asserted in its
tests/test_appliance_sig_v2.py. Change both together and bump the version tag.
"""
import base64
import hashlib

import httpx
import pytest
from nacl.signing import SigningKey

from solar_monitor.cloud import request_sign as rs

GOLDEN_TS = "2026-10-24T09:30:15.123456+00:00"
GOLDEN_BODY = '{"a":"é"}'.encode("utf-8")
GOLDEN_INPUT_PREFIX = (b"v2\n2026-10-24T09:30:15.123456+00:00\nPOST\n"
                       b"/api/ingest/rollups?x=1&y=%20z\n{")
GOLDEN_SHA256 = "26edac623f3ef30e3c1a3905f8d56dbf1ea30c074ce1adb861ee1a33a7699587"


def _golden() -> bytes:
    return rs.signing_input(GOLDEN_TS, "post", "/api/ingest/rollups?x=1&y=%20z", GOLDEN_BODY)


def test_signing_input_matches_golden_vector():
    assert _golden().startswith(GOLDEN_INPUT_PREFIX)
    assert hashlib.sha256(_golden()).hexdigest() == GOLDEN_SHA256


def test_method_is_uppercased_and_binds_path_and_query():
    base = rs.signing_input(GOLDEN_TS, "POST", "/a", b"b")
    assert rs.signing_input(GOLDEN_TS, "post", "/a", b"b") == base
    assert rs.signing_input(GOLDEN_TS, "PATCH", "/a", b"b") != base
    assert rs.signing_input(GOLDEN_TS, "POST", "/b", b"b") != base
    assert rs.signing_input(GOLDEN_TS, "POST", "/a?x=1", b"b") != base


@pytest.mark.parametrize("url,target", [
    ("https://c.example/api/heartbeat", "/api/heartbeat"),
    ("https://c.example/api/heartbeat/command/12", "/api/heartbeat/command/12"),
    ("https://c.example/api/x?a=1&b=two%20words", "/api/x?a=1&b=two%20words"),
    ("https://c.example:8443/api/internal/backups/list", "/api/internal/backups/list"),
])
def test_request_target_is_path_plus_query(url, target):
    assert rs.request_target(url) == target
    # What httpx actually sends on the wire is what we signed.
    assert httpx.Request("GET", url).url.raw_path.decode() == target


class _FakeKp:
    fingerprint = "fp-test"

    def __init__(self):
        self.sk = SigningKey.generate()

    def sign(self, msg: bytes) -> bytes:
        return self.sk.sign(msg).signature


def test_signed_headers_verify_and_bind_endpoint(monkeypatch):
    from solar_monitor.auth import keypair
    kp = _FakeKp()
    monkeypatch.setattr(keypair, "load_or_create", lambda *a, **k: kp)
    body = b'{"status":"success"}'
    h = rs.signed_headers("patch", "https://c.example/api/heartbeat/command/5", body)
    assert h["X-WP-Sig-Version"] == "2"
    assert h["X-WP-Heartbeat-Fp"] == "fp-test"
    sig = base64.urlsafe_b64decode(h["X-WP-Heartbeat-Sig"] + "==")
    ts = h["X-WP-Heartbeat-Ts"]
    vk = kp.sk.verify_key
    vk.verify(rs.signing_input(ts, "PATCH", "/api/heartbeat/command/5", body), sig)
    # The same signature does not verify for another path or method.
    from nacl.exceptions import BadSignatureError
    for method, target in (("PATCH", "/api/heartbeat/command/6"), ("POST", "/api/heartbeat/command/5")):
        with pytest.raises(BadSignatureError):
            vk.verify(rs.signing_input(ts, method, target, body), sig)


def test_keypair_failure_returns_no_headers(monkeypatch):
    from solar_monitor.auth import keypair

    def boom(*a, **k):
        raise RuntimeError("no keypair")
    monkeypatch.setattr(keypair, "load_or_create", boom)
    assert rs.signed_headers("GET", "https://c.example/x", b"") == {}


def test_service_signs_heartbeat_and_command_patch_with_their_own_targets(monkeypatch):
    """Drive the real service: each call signs its own method + URL path and
    sends the exact bytes it signed."""
    import asyncio
    from types import SimpleNamespace
    from solar_monitor.auth import keypair
    from solar_monitor.cloud import service as svc
    from solar_monitor.config import CloudCfg

    kp = _FakeKp()
    monkeypatch.setattr(keypair, "load_or_create", lambda *a, **k: kp)
    sent = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, content=None, headers=None, **k):
            sent.append(("POST", url, content, headers)); return Resp()

        async def patch(self, url, content=None, headers=None, **k):
            sent.append(("PATCH", url, content, headers)); return Resp()

    monkeypatch.setattr(svc.httpx, "AsyncClient", Client)
    s = svc.CloudService(CloudCfg(endpoint="https://cloud.example/", bearer_token="t",
                                  appliance_id=7), SimpleNamespace(store=None))

    async def build():
        return {"x": 1}
    monkeypatch.setattr(s, "_build_payload", build)
    monkeypatch.setattr(s, "_ledger", lambda: SimpleNamespace(record=lambda i: None))

    async def go():
        await s.heartbeat_once()
        await s._patch_command_status(9, "success")
    asyncio.run(go())

    assert [(m, u) for m, u, *_ in sent] == [
        ("POST", "https://cloud.example/api/heartbeat"),
        ("PATCH", "https://cloud.example/api/heartbeat/command/9"),
    ]
    for method, url, content, h in sent:
        assert isinstance(content, bytes)
        assert h["X-WP-Sig-Version"] == "2"
        sig = base64.urlsafe_b64decode(h["X-WP-Heartbeat-Sig"] + "==")
        kp.sk.verify_key.verify(
            rs.signing_input(h["X-WP-Heartbeat-Ts"], method, rs.request_target(url), content), sig)

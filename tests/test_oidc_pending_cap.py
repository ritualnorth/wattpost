"""Anonymous GET /auth/lan/login must not grow oidc_pending.json without bound."""
import json

from solar_monitor.auth import oidc_rp


def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(oidc_rp, "_pending", {})
    monkeypatch.setattr(oidc_rp, "_PENDING_PATH", tmp_path / "oidc_pending.json")
    monkeypatch.setattr(oidc_rp, "_KEY_DIR", tmp_path)


def test_pending_is_capped_and_oldest_dropped(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(oidc_rp, "_now", lambda: clock[0])
    states = []
    for i in range(1500):
        clock[0] += 0.01
        states.append(oidc_rp.stash_pending(pkce_verifier="v", nonce="n", return_to="/"))
    assert len(oidc_rp._pending) == oidc_rp.MAX_PENDING
    on_disk = json.loads((tmp_path / "oidc_pending.json").read_text())
    assert len(on_disk) == oidc_rp.MAX_PENDING
    assert (tmp_path / "oidc_pending.json").stat().st_size < 10_000
    # the newest flows survive, the oldest are gone
    assert oidc_rp.consume_pending(states[-1]) is not None
    assert oidc_rp.consume_pending(states[0]) is None


def test_expired_entries_are_dropped(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(oidc_rp, "_now", lambda: clock[0])
    old = oidc_rp.stash_pending(pkce_verifier="v", nonce="n", return_to="/")
    clock[0] += oidc_rp.STATE_TTL_SECONDS + 1
    new = oidc_rp.stash_pending(pkce_verifier="v", nonce="n", return_to="/")
    assert old not in oidc_rp._pending and new in oidc_rp._pending
    assert oidc_rp.consume_pending(old) is None

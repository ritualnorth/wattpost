"""Static-IP keyfile-block builder in wattpost-helperd (#2).

`_ipv4_section` turns the daemon's validated request into the `[ipv4]`
stanza of an NM keyfile. It's the one bit of the static-IP path with real
logic — address validation + the keyfile format NetworkManager expects —
so it's worth locking down without a host/nmcli. (The activation itself,
`nmcli connection up`, needs a real radio and is covered by on-device
testing.)

The helper ships as an extension-less script in packaging/sbin, so we
load it by path. Importing is side-effect-free — `main()` is guarded by
`__main__`.
"""
import importlib.machinery
import importlib.util
import os

import pytest

_HELPER = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "packaging", "sbin", "wattpost-helperd",
)


def _load_helper():
    # Extension-less script → give it a source loader explicitly.
    loader = importlib.machinery.SourceFileLoader("wattpost_helperd", _HELPER)
    spec = importlib.util.spec_from_loader("wattpost_helperd", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helperd = _load_helper()
ipv4_section = helperd._ipv4_section


def test_default_is_dhcp():
    # No ipv4 block at all → automatic.
    assert ipv4_section({}) == "[ipv4]\nmethod=auto\n"
    # method=auto explicitly → automatic.
    assert ipv4_section({"ipv4": {"method": "auto"}}) == "[ipv4]\nmethod=auto\n"


def test_manual_full():
    sec = ipv4_section({"ipv4": {
        "method": "manual", "address": "192.168.1.50", "prefix": 24,
        "gateway": "192.168.1.1", "dns": "1.1.1.1, 8.8.8.8",
    }})
    assert "method=manual" in sec
    assert "address1=192.168.1.50/24,192.168.1.1" in sec
    assert "dns=1.1.1.1;8.8.8.8;" in sec


def test_manual_address_only_defaults_prefix_24():
    sec = ipv4_section({"ipv4": {"method": "manual", "address": "10.0.0.5"}})
    assert "address1=10.0.0.5/24\n" in sec
    # No gateway appended, no dns line.
    assert "," not in sec.split("address1=")[1].split("\n")[0]
    assert "dns=" not in sec


def test_dns_accepts_comma_or_space():
    for raw in ("1.1.1.1,8.8.8.8", "1.1.1.1 8.8.8.8", "1.1.1.1,  8.8.8.8"):
        sec = ipv4_section({"ipv4": {
            "method": "manual", "address": "192.168.0.2", "dns": raw,
        }})
        assert "dns=1.1.1.1;8.8.8.8;" in sec


@pytest.mark.parametrize("bad", [
    {"method": "manual", "address": "not-an-ip"},
    {"method": "manual", "address": "192.168.1.999"},
    {"method": "manual", "address": "192.168.1.5", "prefix": 0},
    {"method": "manual", "address": "192.168.1.5", "prefix": 33},
    {"method": "manual", "address": "192.168.1.5", "prefix": "x"},
    {"method": "manual", "address": "192.168.1.5", "gateway": "nope"},
    {"method": "manual", "address": "192.168.1.5", "dns": "1.1.1.1, bogus"},
])
def test_bad_input_raises(bad):
    with pytest.raises(ValueError):
        ipv4_section({"ipv4": bad})

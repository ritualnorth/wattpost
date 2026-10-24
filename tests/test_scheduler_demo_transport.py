"""Demo mode swaps in a SyntheticPoller that has no transports; asking the scheduler
for one (the Setup screen's transport list does) must give None, not crash with a 500."""
from types import SimpleNamespace

from solar_monitor.scheduler import PollScheduler


def test_get_transport_with_a_poller_that_has_no_transports():
    sched = SimpleNamespace(_poller=SimpleNamespace())          # like SyntheticPoller
    assert PollScheduler.get_transport(sched, "ble0") is None


def test_get_transport_without_a_poller_and_with_one():
    assert PollScheduler.get_transport(SimpleNamespace(_poller=None), "x") is None
    live = SimpleNamespace(_poller=SimpleNamespace(_transports={"ble0": "T"}))
    assert PollScheduler.get_transport(live, "ble0") == "T"
    assert PollScheduler.get_transport(live, "other") is None

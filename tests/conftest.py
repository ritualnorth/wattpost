import pytest


@pytest.fixture(autouse=True)
def _reset_jwks_refresh_limiter():
    from solar_monitor.cloud import command_verify
    command_verify.reset_refresh_limiter()
    yield
    command_verify.reset_refresh_limiter()

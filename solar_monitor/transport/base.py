"""Transport abstract base class.

A Transport is a connection to one or more Modbus devices. It does not know
about device IDs, registers, parsers, or vendors, it only knows how to ship
bytes and wait for the response.

Each driver builds Modbus RTU frames (slave_id + function + payload + CRC)
and asks the transport to `request()` them. The transport is responsible for
fragmentation, MTU, retries, and timing, *not* protocol semantics.
"""
from __future__ import annotations

import abc
from typing import Any


class TransportError(Exception):
    """Base for all transport-layer errors."""


class TransportTimeout(TransportError):
    """The device did not respond within the expected time."""


class Transport(abc.ABC):
    """One open connection to a bus that carries Modbus RTU frames."""

    #: A short, stable identifier (set by config); used in logs and metrics.
    id: str

    @abc.abstractmethod
    async def open(self) -> None:
        """Establish the underlying connection. Idempotent."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Tear down the connection. Idempotent."""

    @abc.abstractmethod
    async def request(
        self,
        frame: bytes,
        expected_response_len: int,
        timeout: float = 5.0,
    ) -> bytes:
        """Send a Modbus RTU frame and return the response.

        Args:
            frame: Complete RTU frame including 2-byte CRC.
            expected_response_len: How many bytes the response should be.
                The transport uses this to know when fragmented responses are
                complete.
            timeout: Seconds to wait before raising TransportTimeout.
        """

    async def __aenter__(self) -> "Transport":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

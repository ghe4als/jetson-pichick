"""Tiny async NATS publisher with reconnection.

Lossy on disconnect: we don't queue. The Jetson is on wired LAN to a
broker that's nominally always-up; if NATS goes down for 5 minutes we'd
rather show up "missing data for 5 minutes" in the dashboard than spew
a backlog after it's back.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


class NatsPublisher:
    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: NATS | None = None

    async def start(self) -> None:
        self._nc = NATS()
        try:
            await self._nc.connect(
                servers=[self._url],
                allow_reconnect=True,
                max_reconnect_attempts=-1,
                reconnect_time_wait=2.0,
                ping_interval=20,
                connect_timeout=5,
            )
            log.info("nats: connected to %s", _redact(self._url))
        except Exception as e:
            log.warning(
                "nats: initial connect to %s failed (%s); will keep retrying in background",
                _redact(self._url), e,
            )

    async def stop(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception as e:
                log.debug("nats drain failed: %s", e)
            self._nc = None

    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        log.info("nats >> %s %s", subject, data.decode())
        if self._nc is None or not self._nc.is_connected:
            log.warning("nats: drop publish (%s) — not connected", subject)
            return
        try:
            await self._nc.publish(subject, data)
        except Exception as e:
            log.warning("nats publish %s failed: %s", subject, e)


def _redact(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.split("@", 1)
    return f"{scheme}://***@{host}"

"""Regression for executor actions leaving a timed-out lock waiter behind."""
import asyncio
import concurrent.futures
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from custom_components.sigma_connect_ha.sigma_client import SigmaClient


class ObservedLock(asyncio.Lock):
    def __init__(self):
        super().__init__()
        self.waiter_started = asyncio.Event()

    async def acquire(self):
        if self.locked():
            self.waiter_started.set()
        return await super().acquire()


def make_client(lock):
    coordinator = SimpleNamespace(lock=lock, hass=SimpleNamespace(loop=asyncio.get_running_loop()))
    client = SigmaClient("http://alarm.example:5053", "example", "example", "1111",
                         coordinator, send_analytics=False)
    client.session.close()
    client.session = MagicMock()
    return client


@pytest.mark.asyncio
async def test_timed_out_action_cannot_acquire_lock_later(monkeypatch):
    lock = ObservedLock()
    await lock.acquire()
    client = make_client(lock)
    monkeypatch.setattr(client, "ACTION_LOCK_TIMEOUT", 0.02, raising=False)
    original_result = concurrent.futures.Future.result

    def short_old_deadline(future, timeout=None):
        # Also exercise the original implementation without waiting 60 seconds.
        return original_result(future, timeout=0.04 if timeout == 60 else timeout)

    monkeypatch.setattr(concurrent.futures.Future, "result", short_old_deadline)
    worker = asyncio.create_task(asyncio.to_thread(client.perform_action, "disarm"))
    try:
        await asyncio.wait_for(lock.waiter_started.wait(), 1)
        assert await asyncio.wait_for(worker, 1) is False
        assert lock.locked()  # The original polling owner is still in control.
        client.session.get.assert_not_called()
        lock.release()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not lock.locked(), "A timed-out action stole the lock after returning"
        await asyncio.wait_for(lock.acquire(), 0.1)
        lock.release()
    finally:
        if lock.locked():
            lock.release()
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_waiting_action_runs_when_owner_releases_before_deadline(monkeypatch):
    lock = ObservedLock()
    await lock.acquire()
    client = make_client(lock)
    monkeypatch.setattr(client, "ACTION_LOCK_TIMEOUT", 1)
    client.select_partition = MagicMock(return_value=object())
    client._extract_zones_url = MagicMock(return_value="zones.html")
    client.session.get.return_value.text = "<html></html>"
    client.parse_zones_html = MagicMock(return_value={"alarm_status": "AΦOΠΛIΣMENO"})
    monkeypatch.setattr("custom_components.sigma_connect_ha.sigma_client.time.sleep", lambda _: None)
    worker = asyncio.create_task(asyncio.to_thread(client.perform_action, "disarm"))
    try:
        await asyncio.wait_for(lock.waiter_started.wait(), 1)
        assert not worker.done()
        lock.release()
        assert await asyncio.wait_for(worker, 1) is True
        await asyncio.sleep(0)
        assert not lock.locked()
        client.session.get.assert_called_once()
    finally:
        if lock.locked():
            lock.release()
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

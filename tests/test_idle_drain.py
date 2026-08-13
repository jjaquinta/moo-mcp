"""Unit tests for moo_mcp.connection.drain_idle - the idle-timeout read loop
behind send_raw/send_command. Uses a real asyncio.Queue, no socket needed."""

import asyncio
import contextlib
import time

import pytest

from moo_mcp.connection import drain_idle

# Small values keep the suite fast while staying well above scheduler jitter.
IDLE = 0.05
MAX_WAIT = 0.3


@pytest.mark.asyncio
async def test_empty_queue_returns_quickly_at_idle():
    queue: asyncio.Queue[str] = asyncio.Queue()
    start = time.monotonic()
    result = await drain_idle(queue, idle=IDLE, max_wait=MAX_WAIT)
    elapsed = time.monotonic() - start
    assert result == []
    assert elapsed < MAX_WAIT


@pytest.mark.asyncio
async def test_items_within_idle_gap_all_collected_in_order():
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def producer():
        for item in ("one", "two", "three"):
            await queue.put(item)
            await asyncio.sleep(IDLE / 4)

    task = asyncio.create_task(producer())
    result = await drain_idle(queue, idle=IDLE, max_wait=MAX_WAIT)
    await task
    assert result == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_gap_over_idle_stops_collection_early():
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def producer():
        await queue.put("early")
        await asyncio.sleep(IDLE * 4)  # exceeds idle - drain should already have returned
        await queue.put("late")

    task = asyncio.create_task(producer())
    result = await drain_idle(queue, idle=IDLE, max_wait=MAX_WAIT)
    assert result == ["early"]
    await task
    # "late" was pushed after drain_idle returned; not our concern here, just
    # draining it so the queue doesn't leak into another test.
    assert await queue.get() == "late"


@pytest.mark.asyncio
async def test_continuous_traffic_bounds_at_max_wait():
    queue: asyncio.Queue[str] = asyncio.Queue()
    stop = asyncio.Event()

    async def producer():
        i = 0
        while not stop.is_set():
            await queue.put(f"item-{i}")
            i += 1
            await asyncio.sleep(IDLE / 4)

    task = asyncio.create_task(producer())
    start = time.monotonic()
    result = await drain_idle(queue, idle=IDLE, max_wait=MAX_WAIT)
    elapsed = time.monotonic() - start
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(result) > 0
    # Bounded near max_wait despite traffic never going idle.
    assert MAX_WAIT * 0.8 <= elapsed <= MAX_WAIT * 1.5

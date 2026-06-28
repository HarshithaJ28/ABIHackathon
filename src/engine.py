"""Adaptive concurrency ingestion engine.

Two published algorithms power this:

1. Netflix Gradient2 adaptive limiter, derived from TCP Vegas.
2. AWS Exponential Backoff and Jitter for retry scheduling.

The result is a self-tuning worker pool that absorbs the stochastic 429 storm
instead of serializing on it.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import httpx

from config import (
    BACKOFF_BASE,
    BACKOFF_CAP,
    BASE_URL,
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    CONCURRENCY_START,
    HONOR_RETRY_AFTER,
    LIMITER_SMOOTHING,
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_AFTER_CAP,
    RTT_TOLERANCE,
)


class GradientLimiter:
    """Netflix Gradient2-style RTT-driven adaptive limiter."""

    def __init__(self) -> None:
        self.limit = float(CONCURRENCY_START)
        self._rtt_noload: Optional[float] = None
        self._rtt_window: deque[float] = deque(maxlen=200)

    def _update_noload(self, rtt: float) -> float:
        self._rtt_window.append(rtt)
        observed_min = min(self._rtt_window)
        if self._rtt_noload is None:
            self._rtt_noload = rtt
        else:
            self._rtt_noload = min(observed_min, self._rtt_noload * 1.02)
        return self._rtt_noload

    def on_sample(self, rtt: float, inflight: int, dropped: bool) -> int:
        if dropped:
            self.limit = max(CONCURRENCY_MIN, self.limit * 0.7)
            return int(self.limit)

        noload = self._update_noload(rtt)
        gradient = max(0.5, min(1.0, (noload * RTT_TOLERANCE) / max(rtt, 1e-6)))
        queue_headroom = max(1.0, self.limit ** 0.5)

        new_limit = self.limit * gradient + queue_headroom
        if new_limit > self.limit and inflight < self.limit * 0.75:
            new_limit = self.limit

        self.limit = (1 - LIMITER_SMOOTHING) * self.limit + LIMITER_SMOOTHING * new_limit
        self.limit = max(CONCURRENCY_MIN, min(CONCURRENCY_MAX, self.limit))
        return int(self.limit)


class DynamicLimiter:
    """An asyncio gate whose capacity can change at runtime."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.inflight = 0
        self._cond = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._cond:
            while self.inflight >= self.limit:
                await self._cond.wait()
            self.inflight += 1

    async def release(self) -> None:
        async with self._cond:
            self.inflight -= 1
            self._cond.notify(1)

    async def set_limit(self, new_limit: int) -> None:
        async with self._cond:
            grew = new_limit > self.limit
            self.limit = new_limit
            if grew:
                self._cond.notify(new_limit - self.inflight)


@dataclass
class Stats:
    total: int = 0
    completed: int = 0
    failed: int = 0
    attempts: int = 0
    throttled: int = 0
    inflight: int = 0
    limit: int = CONCURRENCY_START
    started_at: float = field(default_factory=time.monotonic)
    _rtts: deque = field(default_factory=lambda: deque(maxlen=300))

    def record_rtt(self, rtt: float) -> None:
        self._rtts.append(rtt)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def rps(self) -> float:
        return self.completed / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def throttle_rate(self) -> float:
        return self.throttled / self.attempts if self.attempts else 0.0

    @property
    def p50_ms(self) -> float:
        if not self._rtts:
            return 0.0
        s = sorted(self._rtts)
        return s[len(s) // 2] * 1000


@dataclass
class Job:
    key: Any
    endpoint: str
    params: Dict[str, Any]
    bucket: str


def _retry_after(resp: httpx.Response) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        wait = float(raw) if raw is not None else BACKOFF_BASE
    except ValueError:
        wait = BACKOFF_BASE
    return min(wait, RETRY_AFTER_CAP)


async def _request_with_retry(
    client: httpx.AsyncClient,
    job: Job,
    limiter: GradientLimiter,
    gate: DynamicLimiter,
    stats: Stats,
) -> Tuple[Any, Union[List, Dict]]:
    """Fetch a single endpoint, honoring Retry-After plus jitter."""
    backoff = BACKOFF_BASE

    for attempt in range(1, MAX_ATTEMPTS + 1):
        await gate.acquire()
        stats.inflight = gate.inflight
        stats.attempts += 1
        t0 = time.monotonic()
        dropped = False
        wait = 0.0
        try:
            resp = await client.get(job.endpoint, params=job.params)
            rtt = time.monotonic() - t0

            if resp.status_code == 200:
                stats.record_rtt(rtt)
                new_limit = limiter.on_sample(rtt, gate.inflight, dropped=False)
                await gate.set_limit(new_limit)
                stats.limit = new_limit
                return job.key, resp.json()

            if resp.status_code == 429:
                stats.throttled += 1
                dropped = stats.throttle_rate > 0.45
                wait = _retry_after(resp) if HONOR_RETRY_AFTER else 0.0
                wait += random.uniform(0, BACKOFF_BASE)
            else:
                resp.raise_for_status()
                return job.key, resp.json()

        except (httpx.TimeoutException, httpx.TransportError):
            dropped = True
            backoff = min(BACKOFF_CAP, max(BACKOFF_BASE, backoff * 3) * random.random())
            wait = backoff

        finally:
            await gate.release()
            new_limit = limiter.on_sample(time.monotonic() - t0, gate.inflight, dropped)
            await gate.set_limit(new_limit)
            stats.limit = new_limit

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(wait)

    stats.failed += 1
    return job.key, []


async def run_jobs(
    jobs: List[Job],
    stats: Stats,
    on_tick: Optional[Callable[[Stats], Awaitable[None]]] = None,
) -> Dict[str, Dict[Any, Union[List, Dict]]]:
    """Execute all jobs concurrently through the adaptive engine."""
    stats.total = len(jobs)
    limiter = GradientLimiter()
    gate = DynamicLimiter(CONCURRENCY_START)
    results: Dict[str, Dict[Any, Union[List, Dict]]] = {}

    limits = httpx.Limits(
        max_connections=CONCURRENCY_MAX,
        max_keepalive_connections=CONCURRENCY_MAX,
    )
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=REQUEST_TIMEOUT,
        limits=limits,
        http2=True,
    ) as client:

        async def worker(job: Job) -> None:
            key, data = await _request_with_retry(client, job, limiter, gate, stats)
            results.setdefault(job.bucket, {})[key] = data
            stats.completed += 1

        tasks = [asyncio.create_task(worker(job)) for job in jobs]

        if on_tick:

            async def ticker() -> None:
                while stats.completed < stats.total:
                    await on_tick(stats)
                    await asyncio.sleep(0.1)
                await on_tick(stats)

            tick_task = asyncio.create_task(ticker())
            await asyncio.gather(*tasks)
            await tick_task
        else:
            await asyncio.gather(*tasks)

    return results
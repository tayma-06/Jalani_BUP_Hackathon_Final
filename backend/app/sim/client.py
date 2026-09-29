import asyncio
import random
import time

import httpx

from app import metrics
from app.config import Settings
from app.sim.schemas import Instance, Snapshot


class SimError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(f"{code}: {message}")


class SimulatorClient:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.simulator_url.rstrip("/"), transport=transport,
            timeout=httpx.Timeout(settings.sim_timeout_seconds, connect=2),
        )
        self.failures = 0
        self.open_until = 0.0

    async def close(self):
        await self.http.aclose()

    async def request(self, method: str, path: str, body=None, params=None):
        admin = path.startswith("/admin")
        if not admin and time.monotonic() < self.open_until:
            raise SimError(503, "CIRCUIT_OPEN", "Simulator circuit is cooling down")
        attempts = 3 if method == "GET" and not admin else 1
        # Allocation uncertainty is resolved against the ledger before any replay.
        for attempt in range(attempts):
            start = time.monotonic()
            status = "transport_error"
            try:
                response = await self.http.request(method, path, json=body, params=params)
                status = str(response.status_code)
                try:
                    data = response.json()
                except ValueError as exc:
                    raise SimError(502, "INVALID_RESPONSE", "Expected JSON from simulator") from exc
                if response.is_error:
                    detail = data.get("detail", data.get("error", {})) if isinstance(data, dict) else {}
                    if isinstance(detail, dict):
                        code, message = detail.get("code", "SIMULATOR_ERROR"), detail.get("message", "Request rejected")
                    else:
                        code, message = "VALIDATION_ERROR", str(detail)[:500]
                    raise SimError(response.status_code, code, message)
                if not admin:
                    self.failures = 0
                    self.open_until = 0
                    metrics.circuit.set(0)
                return data, response.headers.get("X-Simulator-Stale", "").lower() == "true"
            except (httpx.TransportError, SimError) as exc:
                error = exc if isinstance(exc, SimError) else SimError(503, "TRANSPORT_ERROR", type(exc).__name__)
                if error.status < 500:
                    raise error
                if not admin:
                    self.failures += 1
                    if self.failures >= self.settings.breaker_failures:
                        self.open_until = time.monotonic() + self.settings.breaker_seconds
                        metrics.circuit.set(1)
                if attempt + 1 == attempts or self.open_until > time.monotonic():
                    raise error
                await asyncio.sleep(self.settings.retry_base_seconds * 2**attempt + random.uniform(0, 0.02))
            finally:
                endpoint = path if "allocations/" not in path else "/v1/allocations/:id/cancel"
                metrics.sim_requests.labels(endpoint, status).inc()
                metrics.sim_duration.labels(endpoint).observe(time.monotonic() - start)
        raise SimError(503, "UNAVAILABLE", "No simulator response")

    async def gather(self, calls):
        """Await every request to completion before surfacing a failure.

        Plain `asyncio.gather` raises on the first error and leaves its siblings running as
        orphans. At one refresh per second against a flaky simulator those pile up and hold
        connections, so all results are collected first and the earliest failure is re-raised.
        """
        results = await asyncio.gather(*calls, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def snapshot(self, since_tick=0):
        first, first_stale = await self.request("GET", "/v1/instance")
        start = Instance.model_validate(first)
        names = {"regions": "regions", "depots": "depots", "stations": "stations", "routes": "routes",
                 "supply": "supply-arrivals", "events": "events", "allocations": "allocations", "metrics": "metrics"}
        results = await self.gather(self.request("GET", f"/v1/{name}") for name in names.values())
        data = {key: result[0] for key, result in zip(names, results)}
        stale = first_stale or any(result[1] for result in results)
        # Per-station retrieval avoids losing rows when the network grows.
        limit = min(2000, max(48, (max(0, start.tick - since_tick) + 2) * 3))
        histories = await self.gather(self.request("GET", "/v1/demand-history", params={
            "station_id": s["id"], "limit": limit
        }) for s in data["stations"])
        data["history"] = [row for history, _ in histories for row in history]
        stale = stale or any(flag for _, flag in histories)
        last, last_stale = await self.request("GET", "/v1/instance")
        end = Instance.model_validate(last)
        same_world = (start.id, start.seed, start.scenario_id) == (end.id, end.seed, end.scenario_id)
        consistent = same_world and 0 <= end.tick - start.tick <= self.settings.snapshot_tick_span
        data.update(instance=last, start_tick=start.tick, end_tick=end.tick, consistent=consistent)
        return Snapshot.model_validate(data), stale or last_stale

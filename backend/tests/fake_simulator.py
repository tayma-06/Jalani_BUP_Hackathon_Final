"""HTTP fixtures for tests only. This is NOT the official fuel simulator."""
import copy
import json
from datetime import datetime, timedelta, timezone

import httpx

FUELS = ["DIESEL", "PETROL", "OCTANE"]


def world():
    regions = [{"id": "region-dhaka", "name": "Dhaka Division", "demand_factor": 1.0},
               {"id": "region-chattogram", "name": "Chattogram Division", "demand_factor": 1.08}]
    depots = [{"id": "depot-gazipur", "name": "Gazipur Depot", "region_id": regions[0]["id"], "status": "OPEN",
               "dispatch_capacity_per_tick": 12000, "capacity": dict.fromkeys(FUELS, 90000), "inventory": dict.fromkeys(FUELS, 45000)},
              {"id": "depot-patiya", "name": "Patiya Depot", "region_id": regions[1]["id"], "status": "OPEN",
               "dispatch_capacity_per_tick": 11000, "capacity": dict.fromkeys(FUELS, 85000), "inventory": dict.fromkeys(FUELS, 40000)}]
    stations = []
    for id_, name, region, profile, qty in [
        ("station-mirpur", "Mirpur Fuel Station", regions[0]["id"], "urban_high", 2800),
        ("station-tongi", "Tongi Fuel Station", regions[0]["id"], "industrial", 1500),
        ("station-karnaphuli", "Karnaphuli Fuel Station", regions[1]["id"], "highway", 4800),
        ("station-coxsbazar", "Cox's Bazar Fuel Station", regions[1]["id"], "regional", 3800),
    ]:
        stations.append({"id": id_, "name": name, "region_id": region, "demand_profile": profile,
                         "demand_multiplier": 1.0, "status": "OPEN", "capacity": dict.fromkeys(FUELS, 15000),
                         "inventory": dict.fromkeys(FUELS, qty)})
    routes = []
    for d, s, ticks, cap in [(0, 0, 2, 7000), (0, 1, 2, 6500), (1, 2, 2, 7000), (1, 3, 3, 6000), (0, 2, 4, 5000), (1, 0, 4, 5000)]:
        routes.append({"id": f"route-{depots[d]['id'][6:]}-{stations[s]['id'][8:]}", "source_depot_id": depots[d]["id"],
                       "destination_station_id": stations[s]["id"], "transit_ticks": ticks, "max_shipment": cap, "status": "AVAILABLE"})
    return {"instance": {"id": 1, "scenario_id": "test-fixture", "scenario_version": "1", "seed": 12345,
                         "sim_time": "2026-01-01T06:00:00+00:00", "tick": 24, "tick_minutes": 15, "status": "PAUSED"},
            "regions": regions, "depots": depots, "stations": stations, "routes": routes,
            "supply-arrivals": [{"id": "fixture-supply-1", "depot_id": depots[0]["id"], "fuel_type": "DIESEL",
                                  "quantity": 18000, "planned_tick": 40, "actual_tick": None, "status": "SCHEDULED"}],
            "events": [], "allocations": [], "demand-history": [],
            "metrics": {"served_demand_liters": 47200, "unmet_demand_liters": 480, "service_level": 47200 / 47680,
                        "allocation_liters": 0, "allocation_failures": 0}}


class FakeSimulator:
    def __init__(self):
        self.data = world()
        self.posts = []
        self.requests = []
        self.stale = False
        self.unavailable = False
        self.lose_response = False
        self.reject_code = None
        self.moving = False
        self.history_ticks = 0

    def record_demand(self, ticks=1):
        """Append `ticks` of per-station demand rows, newest last, for history-merge tests."""
        base = self.data["demand-history"]
        for tick in range(self.data["instance"]["tick"] - ticks + 1, self.data["instance"]["tick"] + 1):
            for index, station in enumerate(self.data["stations"]):
                for fuel_index, fuel in enumerate(FUELS):
                    if any(row["tick"] == tick and row["station_id"] == station["id"] for row in base):
                        continue
                    demand = 100 + index * 10 + fuel_index * 5 + tick
                    base.append({"id": tick * 100 + index * 10 + fuel_index, "station_id": station["id"],
                                 "fuel_type": fuel, "tick": tick,
                                 "sim_time": (datetime(2026, 1, 1, tzinfo=timezone.utc)
                                              + timedelta(minutes=tick * 15)).isoformat(),
                                 "demand_liters": demand, "served_liters": demand - 1, "unmet_liters": 1,
                                 "demand_multiplier": 1.0})
        self.history_ticks += ticks

    def transport(self):
        return httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.requests.append((request.method, request.url.path))
        path = request.url.path
        if path == "/v1/stream":
            return httpx.Response(503, json={"detail": {"code": "FIXTURE_NO_STREAM"}})
        if path.startswith("/admin/"):
            action = path.removeprefix("/admin/")
            body = json.loads(request.content) if request.content else {}
            if action == "faults/clear":
                self.stale = self.unavailable = False
            elif action == "faults":
                self.stale = body["type"] == "stale_data"
                self.unavailable = body["type"] == "unavailable"
            elif action in {"run", "pause"}:
                self.data["instance"]["status"] = "RUNNING" if action == "run" else "PAUSED"
            elif action == "reset":
                self.data = world()
                self.data["instance"]["tick"] = 0
                self.data["instance"]["sim_time"] = "2026-01-01T00:00:00+00:00"
            elif action == "step":
                self.data["instance"]["tick"] += 1
                self.data["instance"]["sim_time"] = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=self.data["instance"]["tick"] * 15)).isoformat()
            elif action == "events":
                self.data["events"].append({"id": len(self.data["events"]) + 1, "status": "SCHEDULED", "end_tick": body["start_tick"] + body["duration_ticks"], **body})
            return httpx.Response(200, json={"status": "ok"})
        if self.unavailable and path != "/v1/health":
            return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "Fixture unavailable"}})
        headers = {"X-Simulator-Stale": "true"} if self.stale else {}
        if request.method == "GET":
            key = path.removeprefix("/v1/")
            if key == "health":
                return httpx.Response(200, json={"status": "ok"})
            if key == "demand-history":
                station = request.url.params.get("station_id")
                limit = int(request.url.params.get("limit", "100"))
                rows = [r for r in self.data["demand-history"] if r["station_id"] == station]
                return httpx.Response(200, json=rows[-limit:], headers=headers)
            if key == "instance" and self.moving:
                self.data["instance"]["tick"] += 10
            return httpx.Response(200, json=copy.deepcopy(self.data[key]), headers=headers)
        if path == "/v1/allocations":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.reject_code:
                return httpx.Response(409, json={"detail": {"code": self.reject_code, "message": "Fixture rejection"}})
            existing = next((a for a in self.data["allocations"] if a["idempotency_key"] == body["idempotency_key"]), None)
            if existing:
                if any(existing[k] != v for k, v in body.items()):
                    return httpx.Response(409, json={"detail": {"code": "IDEMPOTENCY_KEY_MISMATCH"}})
                return httpx.Response(200, json=existing)
            allocation = {**body, "id": len(self.data["allocations"]) + 1, "created_tick": self.data["instance"]["tick"],
                          "status": "PENDING", "departure_tick": None, "expected_arrival_tick": None,
                          "actual_arrival_tick": None, "failure_reason": None}
            self.data["allocations"].append(allocation)
            for d in self.data["depots"]:
                if d["id"] == body["source_depot_id"]:
                    d["inventory"][body["fuel_type"]] -= body["quantity"]
            if self.lose_response:
                self.unavailable = True
                raise httpx.ReadTimeout("Fixture lost accepted response", request=request)
            return httpx.Response(201, json=allocation)
        if path.endswith("/cancel"):
            allocation = next(a for a in self.data["allocations"] if a["id"] == int(path.split("/")[-2]))
            if allocation["status"] != "PENDING":
                return httpx.Response(409, json={"detail": {"code": "CANNOT_CANCEL"}})
            allocation["status"] = "CANCELLED"
            for d in self.data["depots"]:
                if d["id"] == allocation["source_depot_id"]:
                    d["inventory"][allocation["fuel_type"]] += allocation["quantity"]
            return httpx.Response(200, json=allocation)
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND"}})

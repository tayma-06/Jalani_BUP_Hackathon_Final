"""Organizer API schemas. Unknown fields are allowed; unsafe values are rejected."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Fuel = Literal["DIESEL", "PETROL", "OCTANE"]
FUELS = ("DIESEL", "PETROL", "OCTANE")
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Instance(Schema):
    id: int
    scenario_id: str
    scenario_version: str = "unknown"
    seed: int
    sim_time: datetime
    tick: int = Field(ge=0)
    tick_minutes: int = Field(gt=0)
    status: Literal["PAUSED", "RUNNING"]


class Region(Schema):
    id: str
    name: str
    demand_factor: Positive


class Tank(Schema):
    id: str
    name: str
    region_id: str
    capacity: dict[Fuel, Positive]
    inventory: dict[Fuel, Nonnegative]

    @model_validator(mode="after")
    def valid_tanks(self):
        if set(self.capacity) != set(FUELS) or set(self.inventory) != set(FUELS):
            raise ValueError("Every tank must contain all three fuel types")
        if any(self.inventory[f] > self.capacity[f] + 0.01 for f in FUELS):
            raise ValueError("Inventory exceeds capacity")
        return self


class Depot(Tank):
    status: Literal["OPEN", "CONSTRAINED"]
    dispatch_capacity_per_tick: Positive


class Station(Tank):
    status: Literal["OPEN", "OUTAGE"]
    demand_profile: str
    demand_multiplier: Nonnegative


class Route(Schema):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = Field(ge=0)
    max_shipment: Positive
    status: Literal["AVAILABLE", "DISRUPTED"]


class Supply(Schema):
    id: str
    depot_id: str
    fuel_type: Fuel
    quantity: Nonnegative
    planned_tick: int = Field(ge=0)
    actual_tick: int | None = None
    status: Literal["SCHEDULED", "DELAYED", "ARRIVED"]


class Event(Schema):
    id: int
    type: str
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    status: Literal["SCHEDULED", "ACTIVE", "RESOLVED"]
    parameters: dict = Field(default_factory=dict)


class AllocationBody(Schema):
    idempotency_key: str = Field(min_length=1, max_length=150)
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: Fuel
    quantity: Positive


class Allocation(AllocationBody):
    id: int
    created_tick: int = Field(ge=0)
    departure_tick: int | None = None
    expected_arrival_tick: int | None = None
    actual_arrival_tick: int | None = None
    status: Literal["PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"]
    failure_reason: str | None = None


class Demand(Schema):
    id: int
    station_id: str
    fuel_type: Fuel
    tick: int = Field(ge=0)
    sim_time: datetime
    demand_liters: Nonnegative
    served_liters: Nonnegative
    unmet_liters: Nonnegative
    demand_multiplier: Nonnegative | None = None

    @model_validator(mode="after")
    def balanced(self):
        if abs(self.demand_liters - self.served_liters - self.unmet_liters) > 0.05:
            raise ValueError("Demand does not equal served plus unmet")
        return self


class Metrics(Schema):
    served_demand_liters: Nonnegative
    unmet_demand_liters: Nonnegative
    service_level: float = Field(ge=0, le=1)
    allocation_liters: Nonnegative
    allocation_failures: int = Field(ge=0)


class Snapshot(Schema):
    instance: Instance
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    supply: list[Supply]
    events: list[Event]
    allocations: list[Allocation]
    history: list[Demand]
    metrics: Metrics
    start_tick: int = 0
    end_tick: int = 0
    consistent: bool = True

    @model_validator(mode="after")
    def references_exist(self):
        for items in (self.regions, self.depots, self.stations, self.routes, self.allocations):
            if len({x.id for x in items}) != len(items):
                raise ValueError("Duplicate entity identifiers")
        regions = {r.id for r in self.regions}
        depots = {d.id for d in self.depots}
        stations = {s.id for s in self.stations}
        routes = {r.id: r for r in self.routes}
        if not stations or not depots:
            raise ValueError("World has no stations or depots")
        if any(t.region_id not in regions for t in [*self.depots, *self.stations]):
            raise ValueError("Unknown region")
        if any(r.source_depot_id not in depots or r.destination_station_id not in stations
               for r in self.routes):
            raise ValueError("Unknown route endpoint")
        for a in self.allocations:
            r = routes.get(a.route_id)
            if not r or (a.source_depot_id, a.destination_station_id) != (
                r.source_depot_id, r.destination_station_id
            ):
                raise ValueError("Allocation route mismatch")
        if any(s.depot_id not in depots for s in self.supply):
            raise ValueError("Unknown supply depot")
        if any(d.station_id not in stations or d.tick > self.end_tick for d in self.history):
            raise ValueError("Invalid history reference or future observation")
        return self

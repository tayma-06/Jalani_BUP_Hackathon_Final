import asyncio
import csv
import hmac
import io
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import metrics
from app.assistant import AssistantRequest, OperationsAssistant
from app.config import Settings, settings
from app.db import Alert, ForecastEvaluation, ModelVersion, Recommendation, audit
from app.intelligence.engine import recommend
from app.intelligence.ml import MLForecast, ModelError, deploy_model
from app.service import FuelService
from app.sim.client import SimError

logging.basicConfig(level=logging.INFO, format="%(message)s")
security = HTTPBearer(auto_error=False)


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)


class Approval(BaseModel):
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class Rejection(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class AutopilotSetting(BaseModel):
    mode: Literal["manual", "advisory", "auto"]


class PolicySetting(BaseModel):
    version: Literal["greedy_v1", "naive_reorder"]
    weights: dict[Literal["DIESEL", "PETROL", "OCTANE"], float] | None = None

    @model_validator(mode="after")
    def valid_weights(self):
        if self.weights and (set(self.weights) != {"DIESEL", "PETROL", "OCTANE"}
                             or any(not 0.1 <= w <= 10 for w in self.weights.values())):
            raise ValueError("Supply three fuel weights between 0.1 and 10")
        return self


class EventInput(BaseModel):
    type: Literal["demand_spike", "route_disruption", "station_outage", "depot_constraint", "shipment_delay", "supply_shortfall"]
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(gt=0, le=10000)
    parameters: dict = Field(default_factory=dict)


class FaultInput(BaseModel):
    type: Literal["latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"]
    duration_seconds: int = Field(gt=0, le=3600)
    parameters: dict = Field(default_factory=dict)


def create_app(config: Settings = settings, service: FuelService | None = None):
    service = service or FuelService(config)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if config.background_enabled:
            tasks.append(asyncio.create_task(service.run()))
            if config.stream_enabled:
                tasks.append(asyncio.create_task(service.stream()))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await service.client.close()
        service.db.engine.dispose()

    app = FastAPI(title="Jalani · Fuel Supply Intelligence", version=config.app_version, lifespan=lifespan)
    app.state.service = service
    assistant = OperationsAssistant(config)
    app.state.assistant = assistant
    login_attempts = defaultdict(deque)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start
        service.latencies.append(duration)
        service.errors.append(int(response.status_code >= 500))
        route = request.scope.get("route")
        handler = route.path if route else "unmatched"
        metrics.http_requests.labels(handler, request.method, str(response.status_code)).inc()
        metrics.http_duration.labels(handler, request.method).observe(duration)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(SimError)
    async def simulator_error(request, exc):
        return JSONResponse(status_code=503 if exc.status >= 500 else exc.status,
                            content={"detail": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        service.db_ok = False
        service.set_mode("DEGRADED", "Database unavailable; execution blocked")
        return JSONResponse(status_code=503, content={"detail": service.reason})

    def user(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if not credentials:
            raise HTTPException(401, "Sign in to continue")
        try:
            claims = jwt.decode(credentials.credentials, config.jwt_secret, algorithms=["HS256"],
                                options={"require": ["exp", "sub", "role"]}, audience="jalani")
            if claims["role"] not in {"viewer", "operator", "admin"}:
                raise ValueError("Invalid role")
            return claims
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(401, "Session expired; sign in again") from exc

    def operator(claims=Depends(user)):
        if claims["role"] not in {"operator", "admin"}:
            raise HTTPException(403, "Operator access required")
        return claims

    def admin(claims=Depends(user)):
        if claims["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return claims

    async def _reason(request: Request) -> str:
        """An optional operator-supplied justification, read without breaking typed bodies."""
        try:
            body = await request.json()
        except ValueError:
            return "unspecified"
        return str(body.get("reason", "")).strip()[:300] or "unspecified"

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request):
        address = request.client.host if request.client else "local"
        attempts = login_attempts[address]
        now = time.monotonic()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Too many sign-in attempts; try again in one minute")
        role = None
        for candidate in ("viewer", "operator", "admin"):
            matches_user = hmac.compare_digest(body.username.encode(), getattr(config, candidate + "_user").encode())
            matches_password = hmac.compare_digest(body.password.encode(), getattr(config, candidate + "_password").encode())
            if matches_user and matches_password:
                role = candidate
        if role is None:
            attempts.append(now)
            raise HTTPException(401, "Invalid username or password")
        token = jwt.encode({"sub": body.username, "role": role, "aud": "jalani",
                            "exp": datetime.now(timezone.utc) + timedelta(hours=8)}, config.jwt_secret, algorithm="HS256")
        return {"access_token": token, "token_type": "bearer", "role": role, "username": body.username}

    @app.get("/api/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready():
        health = service.health()
        return JSONResponse(status_code=200 if service.safe() else 503, content=health)

    @app.get("/api/health")
    async def health():
        return service.health()

    @app.get("/metrics")
    async def prometheus():
        metrics.age.set(service.age or 0)
        metrics.stale.set(int(not service.safe()))
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/network/state")
    async def network(claims=Depends(user)):
        return service.network()

    @app.get("/api/stations/{station_id}/forecast")
    async def station_forecast(station_id: str, fuel: Literal["DIESEL", "PETROL", "OCTANE"] = "DIESEL", claims=Depends(user)):
        result = service.analysis.get((station_id, fuel))
        if result is None:
            raise HTTPException(404, "Station forecast not available")
        return {"station_id": station_id, "fuel_type": fuel, "start_tick": service.snapshot.instance.tick,
                "tick_minutes": service.snapshot.instance.tick_minutes, **result,
                "uncertainty_note": "Model estimates; independent nonnegative normal residuals, not a calibrated probability."}

    @app.get("/api/recommendations")
    async def recommendations(status: str | None = "PROPOSED", claims=Depends(user)):
        return [r for r in service.recommendations if not status or status == "ALL" or r["status"] == status]

    @app.post("/api/recommendations/{rec_id}/approve")
    async def approve(rec_id: str, body: Approval, claims=Depends(operator)):
        return await service.approve(rec_id, claims["sub"], body.quantity)

    @app.post("/api/recommendations/{rec_id}/reject")
    async def reject(rec_id: str, body: Rejection, claims=Depends(operator)):
        async with service.lock:
            with service.db.session() as session:
                row = session.get(Recommendation, rec_id, with_for_update=True)
                if not row or row.run_id != service.run_id:
                    raise HTTPException(404, "Recommendation not found")
                if row.status != "PROPOSED":
                    raise HTTPException(409, "Recommendation was already handled")
                row.status = "REJECTED"
                audit(session, claims["sub"], "decision.rejected", rec_id, reason=body.reason)
                session.commit()
            service.event("decision.rejected", recommendation_id=rec_id, reason=body.reason)
            service.load_views()
            return {"status": "REJECTED"}

    @app.post("/api/decide")
    async def decide(dry_run: bool = True, claims=Depends(operator)):
        if not service.snapshot:
            raise HTTPException(503, "No simulator snapshot")
        if not dry_run:
            await service.refresh()
            return service.recommendations
        # CPU work goes to a thread so reads and health endpoints remain responsive.
        return await asyncio.to_thread(recommend, service.snapshot, service.analysis, service.policy,
                                       config.monte_carlo_paths, service.policy_name)

    @app.get("/api/alerts")
    async def alerts(status: str | None = None, claims=Depends(user)):
        return [a for a in service.alerts if not status or status == "ALL" or a["status"] == status]

    @app.post("/api/alerts/{alert_id}/ack")
    async def acknowledge(alert_id: str, claims=Depends(operator)):
        async with service.lock:
            with service.db.session() as session:
                row = session.get(Alert, alert_id)
                if not row or row.run_id != service.run_id:
                    raise HTTPException(404, "Alert not found")
                if row.status != "RESOLVED":
                    row.status = "ACKNOWLEDGED"
                audit(session, claims["sub"], "alert.acknowledged", alert_id)
                session.commit()
            service.load_views()
        return {"status": "ACKNOWLEDGED"}

    @app.get("/api/decisions")
    async def decisions(claims=Depends(user)):
        return service.decisions

    @app.get("/api/decisions/export.csv")
    async def decisions_csv(claims=Depends(user)):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["created_at", "run_id", "actor", "status", "source_depot_id", "destination_station_id", "fuel_type", "quantity"])
        for d in service.decisions:
            values = [d[k] for k in ("created_at", "run_id", "actor", "status")]
            values += [d["request"][k] for k in ("source_depot_id", "destination_station_id", "fuel_type", "quantity")]
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="jalani-decisions.csv"'})

    @app.post("/api/decisions/{execution_id}/retry")
    async def retry(execution_id: str, claims=Depends(operator)):
        return await service.retry(execution_id, claims["sub"])

    @app.post("/api/allocations/{allocation_id}/cancel")
    async def cancel(allocation_id: int, claims=Depends(operator)):
        async with service.lock:
            await service._refresh()
            await service.final_gate()
            allocation = next((a for a in service.snapshot.allocations if a.id == allocation_id), None)
            if not allocation or allocation.status != "PENDING":
                raise HTTPException(409, "Only a pending allocation can be cancelled")
            with service.db.session() as session:
                audit(session, claims["sub"], "allocation.cancel_requested", str(allocation_id))
                session.commit()
            result, _ = await service.client.request("POST", f"/v1/allocations/{allocation_id}/cancel")
            await service._refresh()
            return result

    @app.get("/api/supply")
    async def supply(claims=Depends(user)):
        return [x.model_dump(mode="json") for x in service.snapshot.supply] if service.snapshot else []

    @app.get("/api/events")
    async def events(claims=Depends(user)):
        return [x.model_dump(mode="json") for x in service.snapshot.events] if service.snapshot else []

    @app.get("/api/incidents")
    async def incidents(claims=Depends(user)):
        return service.incidents

    @app.post("/api/assistant")
    async def explain(body: AssistantRequest, claims=Depends(user)):
        return await assistant.explain(service, body)

    @app.get("/api/summary")
    async def summary(claims=Depends(user)):
        state = service.network()
        if not service.snapshot:
            return {"text": "Waiting for simulator data. No operational conclusions are available.", "source": "template"}
        kpi = state["kpis"]
        return {"text": f"At tick {state['tick']}, service level is {kpi['service_level']:.1%}. "
                        f"Unmet demand is {kpi['unmet_liters']:,.0f} L. {kpi['open_alerts']} alerts are open and "
                        f"{kpi['pending_recommendations']} allocation proposals await review. "
                        f"System mode: {state['mode'].lower()}. All figures describe the simulated environment.",
                "source": "template"}

    @app.get("/api/settings/autopilot")
    async def get_autopilot(claims=Depends(user)):
        return {"mode": service.autopilot}

    @app.post("/api/settings/autopilot")
    async def set_autopilot(body: AutopilotSetting, claims=Depends(admin)):
        async with service.lock:
            with service.db.session() as session:
                service.db.put_setting(session, "autopilot", body.model_dump())
                audit(session, claims["sub"], "settings.autopilot", body.mode)
                session.commit()
            service.autopilot = body.mode
            service.build_network()
        return body

    @app.get("/api/settings/policy")
    async def get_policy(claims=Depends(user)):
        return {**service.policy, "version": service.policy_name}

    @app.get("/api/policies")
    async def policies(claims=Depends(user)):
        return service.policy_registry()

    @app.post("/api/settings/policy")
    async def set_policy(body: PolicySetting, request: Request, claims=Depends(admin)):
        async with service.lock:
            service.db.check()
            payload = await _reason(request)
            config = {**service.policy, **body.model_dump(exclude_none=True)}
            return service.activate_policy(body.version, config, claims["sub"], payload)

    @app.post("/api/settings/policy/rollback")
    async def revert_policy(request: Request, claims=Depends(admin)):
        async with service.lock:
            service.db.check()
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if not str(body.get("reason") or "").strip():
                raise HTTPException(422, "A rollback needs a recorded reason")
            return service.rollback_policy(claims["sub"], str(body["reason"])[:300], body.get("to_version"))

    @app.get("/api/models/forecast")
    async def forecast_models(claims=Depends(user)):
        with service.db.session() as session:
            rows = session.scalars(select(ModelVersion).order_by(ModelVersion.activated_at.desc()))
            return {"active": service.forecaster.version if service.forecaster else None,
                    "fallback": "profile_v1" if not service.forecaster else None,
                    "models": [{"version": r.version, "checksum": r.checksum, "active": r.active,
                                "previous_version": r.previous_version, "actor": r.actor, "reason": r.reason,
                                "data_fingerprint": r.data_fingerprint,
                                "feature_schema": r.feature_schema,
                                "training_config": r.training_config,
                                "evaluation": r.evaluation,
                                "activated_at": r.activated_at} for r in rows]}

    @app.post("/api/models/forecast/deploy")
    async def deploy_forecast(body: dict, request: Request, claims=Depends(admin)):
        artifact = body.get("artifact") if isinstance(body.get("artifact"), dict) else body
        try:
            forecaster = MLForecast(artifact)
        except ModelError as exc:
            raise HTTPException(422, str(exc)) from exc
        with service.db.session() as session:
            result = deploy_model(session, artifact, claims["sub"], (await _reason(request)) or "deploy")
        service.forecaster = forecaster
        service.dirty.set()
        return result

    @app.get("/api/models/forecast/evaluations")
    async def forecast_evaluations(station_id: str | None = None, limit: int = 100, claims=Depends(operator)):
        with service.db.session() as session:
            query = select(ForecastEvaluation).order_by(ForecastEvaluation.target_tick.desc()).limit(min(limit, 500))
            if station_id:
                query = query.where(ForecastEvaluation.station_id == station_id)
            return [{"station_id": r.station_id, "fuel_type": r.fuel_type, "target_tick": r.target_tick,
                     "issued_at_tick": r.issued_at_tick, "horizon": r.horizon, "model_version": r.model_version,
                     "predicted": r.predicted, "lower": r.lower, "upper": r.upper, "observed": r.observed}
                    for r in session.scalars(query)]

    async def control(path, payload, claims):
        async with service.lock:
            service.db.check()
            with service.db.session() as session:
                audit(session, claims["sub"], "control.request", path, parameters=payload or {})
                session.commit()
            result, _ = await service.client.request("POST", path, payload)
            if path == "/admin/reset":
                service.reset_notice = True
            await service._refresh()
            return result

    @app.post("/api/control/sim/{action}")
    async def sim_control(action: Literal["run", "pause", "step", "reset"], request: Request, claims=Depends(admin)):
        if action == "reset":
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if body.get("confirm") is not True:
                raise HTTPException(422, "Reset requires confirm: true")
        return await control(f"/admin/{action}", None, claims)

    @app.post("/api/control/events")
    async def inject_event(body: EventInput, claims=Depends(admin)):
        return await control("/admin/events", body.model_dump(), claims)

    @app.post("/api/control/faults")
    async def inject_fault(body: FaultInput, claims=Depends(admin)):
        return await control("/admin/faults", body.model_dump(), claims)

    @app.post("/api/control/faults/clear")
    async def clear_faults(claims=Depends(admin)):
        return await control("/admin/faults/clear", None, claims)

    @app.post("/api/control/chaos/{kind}")
    async def chaos(kind: Literal["corrupt-next", "crash"], claims=Depends(admin)):
        if not config.chaos_enabled:
            raise HTTPException(403, "Demo chaos controls are disabled")
        if kind == "corrupt-next":
            service.corrupt_next = True
            service.dirty.set()
        else:
            asyncio.get_running_loop().call_later(0.25, os._exit, 1)
        return {"status": "scheduled", "kind": kind}

    return app


app = create_app()

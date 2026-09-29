"""Durable state. SQLite supports local development; Compose uses PostgreSQL.

Schema changes go through `MIGRATIONS`: an ordered, additive, audited list. `create_all`
only ever adds tables that do not exist yet, so an older database keeps its rows and its
decision history when a new release starts.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    or_,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

# Retention windows. Each mirrors the widest read in the service layer, so pruning can
# never remove a row that anything still consumes.
KEEP_SNAPSHOTS = 3
KEEP_HISTORY = 18000
KEEP_RECOMMENDATIONS = 300
KEEP_AUDIT = 5000
KEEP_ALERTS = 500
KEEP_INCIDENTS = 500
KEEP_EXECUTIONS = 500
# An execution is still in flight until the simulator ledger reports a terminal state.
OPEN_STATUSES = ("UNKNOWN", "PREPARED", "PENDING", "IN_TRANSIT")


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    created_tick: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True, default="PROPOSED")
    payload: Mapped[dict] = mapped_column(JSON)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recommendation_id: Mapped[str] = mapped_column(String(36), unique=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True)
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="PREPARED")
    actor: Mapped[str] = mapped_column(String(100))
    sim_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class DemandObservation(Base):
    __tablename__ = "demand_obs"
    __table_args__ = (UniqueConstraint("run_id", "sim_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sim_id: Mapped[int] = mapped_column(Integer)
    tick: Mapped[int] = mapped_column(Integer)
    station_id: Mapped[str] = mapped_column(String(150))
    fuel_type: Mapped[str] = mapped_column(String(10))
    demand: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class SnapshotRecord(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    tick: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    payload: Mapped[dict] = mapped_column(JSON)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(220))
    details: Mapped[dict] = mapped_column(JSON)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    applied_at: Mapped[str] = mapped_column(String(40), default=now)


class PolicyVersion(Base):
    """Every distinct policy configuration ever activated, with its rollback target."""
    __tablename__ = "policy_versions"
    version: Mapped[str] = mapped_column(String(60), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    config: Mapped[dict] = mapped_column(JSON)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    previous_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    reason: Mapped[str] = mapped_column(String(300), default="")
    benchmark: Mapped[dict] = mapped_column(JSON, default=dict)
    activated_at: Mapped[str] = mapped_column(String(40), default=now)


class ModelVersion(Base):
    """Trained forecast model registry: provenance, checksum, evaluation and activation."""
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    artifact: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(80), default="")
    feature_schema: Mapped[list] = mapped_column(JSON, default=list)
    data_fingerprint: Mapped[str] = mapped_column(String(80), default="")
    training_config: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict)
    dependencies: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    previous_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    reason: Mapped[str] = mapped_column(String(300), default="")
    activated_at: Mapped[str] = mapped_column(String(40), default=now)


class DriftState(Base):
    """Last drift verdict per monitored signal, used for persistence and recovery logic."""
    __tablename__ = "drift_state"
    __table_args__ = (UniqueConstraint("run_id", "scope", "signal"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(220))
    signal: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    since_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class AgentRun(Base):
    """Typed, inspectable result of one multi-agent coordination cycle."""
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    tick: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)
    status: Mapped[str] = mapped_column(String(30), default="OK")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AppEvent(Base):
    """Durable publication record for the operator event stream (replay, not delivery)."""
    __tablename__ = "app_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Lease(Base):
    """Database-backed leadership. An expired or superseded holder must stop writing."""
    __tablename__ = "leases"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    holder: Mapped[str] = mapped_column(String(120))
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    fence: Mapped[int] = mapped_column(Integer, default=0)
    acquired_at: Mapped[str] = mapped_column(String(40), default=now)
    renewed_at: Mapped[str] = mapped_column(String(40), default=now)


class ForecastEvaluation(Base):
    """Predicted-versus-observed records used for drift and calibration reporting."""
    __tablename__ = "forecast_evaluations"
    __table_args__ = (UniqueConstraint("run_id", "station_id", "fuel_type", "target_tick"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    station_id: Mapped[str] = mapped_column(String(150), index=True)
    fuel_type: Mapped[str] = mapped_column(String(10))
    target_tick: Mapped[int] = mapped_column(Integer, index=True)
    issued_at_tick: Mapped[int] = mapped_column(Integer)
    horizon: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(80))
    predicted: Mapped[float] = mapped_column(Float)
    lower: Mapped[float] = mapped_column(Float)
    upper: Mapped[float] = mapped_column(Float)
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


Index("ix_app_events_run_seq", AppEvent.run_id, AppEvent.seq)


MIGRATIONS = [
    ("0001_baseline",
     "Core tables: settings, recommendations, executions, demand_obs, snapshots, alerts, incidents, audit_log."),
    ("0002_policy_registry",
     "policy_versions and schema_migrations. A single 'policy' setting row is preserved as the "
     "active version so previously stored configuration is not lost."),
    ("0003_model_registry",
     "model_versions: trained forecast provenance, checksum, feature schema, evaluation, activation."),
    ("0004_drift_and_evaluation",
     "drift_state and forecast_evaluations for drift persistence and predicted-versus-observed checks."),
    ("0005_agents_and_stream",
     "agent_runs and app_events for inspectable multi-agent cycles and the operator event stream."),
    ("0006_leases",
     "leases for database-backed leadership so a second replica cannot issue simulator writes."),
]


class Database:
    def __init__(self, url):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        else:
            kwargs["connect_args"] = {"connect_timeout": 2, "options": "-c statement_timeout=3000"}
        self.engine = create_engine(url, **kwargs)
        self.initialized = False
        self.applied = []

    def initialize(self):
        # Additive only: existing tables and rows are never dropped or rewritten.
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            # create_all() only creates missing tables, so indexes that predate an already
            # provisioned database are added here. Both statements are idempotent.
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_executions_created_at ON executions (created_at DESC)"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_demand_obs_run_tick ON demand_obs (run_id, tick DESC)"))
        with self.session() as session:
            done = {row.version for row in session.scalars(select(SchemaMigration))}
            for version, note in MIGRATIONS:
                if version in done:
                    continue
                if version == "0002_policy_registry":
                    self._seed_policy_registry(session)
                session.add(SchemaMigration(version=version, note=note))
                self.applied.append(version)
            session.commit()
        self.initialized = True

    @staticmethod
    def _seed_policy_registry(session):
        """Lift any pre-registry single-row policy setting into the version registry."""
        row = session.get(Setting, "policy")
        if row and not session.get(PolicyVersion, row.value.get("version", "greedy_v1")):
            session.add(PolicyVersion(version=row.value.get("version", "greedy_v1"), config=row.value,
                                      weights=row.value.get("weights", {}), active=True,
                                      actor="system", reason="Imported from the pre-registry policy setting"))

    def migrate(self):
        self.initialize()
        return self.applied

    def session(self):
        return Session(self.engine, expire_on_commit=False)

    def check(self):
        if not self.initialized:
            self.initialize()
        with self.session() as session:
            session.execute(text("SELECT 1"))

    def setting(self, key, default=None):
        with self.session() as session:
            row = session.get(Setting, key)
            return row.value if row else default

    @staticmethod
    def put_setting(session, key, value):
        row = session.get(Setting, key)
        if row:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))

    def last_snapshot(self, run_id=None):
        query = select(SnapshotRecord).order_by(SnapshotRecord.id.desc()).limit(1)
        if run_id:
            query = query.where(SnapshotRecord.run_id == run_id)
        with self.session() as session:
            return session.scalar(query)

    def open_executions(self, session, run_id):
        """Rows the refresh loop can still change: this run, plus any older unresolved intent.

        Terminal rows from earlier runs are skipped entirely, so this stays cheap as the
        shipment history grows.
        """
        return session.scalars(
            select(Execution)
            .where(or_(Execution.run_id == run_id, Execution.status.in_(OPEN_STATUSES)))
            .order_by(Execution.created_at.desc())
        )

    def prune(self, session, run_id):
        """Bound durable growth. Only rows that nothing can read again are removed.

        A run_id is minted on every simulator reset, so rows from other runs are unreachable:
        the restore path reads the current run's newest snapshot, the history merge reads the
        current run's newest observations, and the decision list reads the current run.
        """
        session.execute(delete(SnapshotRecord).where(SnapshotRecord.run_id != run_id))
        session.execute(delete(DemandObservation).where(DemandObservation.run_id != run_id))
        session.execute(
            delete(Recommendation).where(
                Recommendation.run_id != run_id,
                Recommendation.id.not_in(select(Execution.recommendation_id)),
            )
        )
        session.execute(delete(SnapshotRecord).where(
            SnapshotRecord.run_id == run_id,
            SnapshotRecord.id.not_in(
                select(SnapshotRecord.id).where(SnapshotRecord.run_id == run_id)
                .order_by(SnapshotRecord.id.desc()).limit(KEEP_SNAPSHOTS)
            ),
        ))
        session.execute(delete(DemandObservation).where(
            DemandObservation.run_id == run_id,
            DemandObservation.id.not_in(
                select(DemandObservation.id).where(DemandObservation.run_id == run_id)
                .order_by(DemandObservation.tick.desc()).limit(KEEP_HISTORY)
            ),
        ))
        session.execute(delete(Recommendation).where(
            Recommendation.run_id == run_id,
            Recommendation.status == "EXPIRED",
            Recommendation.id.not_in(
                select(Recommendation.id).where(Recommendation.run_id == run_id,
                                                Recommendation.status == "EXPIRED")
                .order_by(Recommendation.created_tick.desc()).limit(KEEP_RECOMMENDATIONS)
            ),
        ))
        # Alerts and incidents are read per run, so an older run's rows are unreachable. The
        # Alertmanager rows carry run_id "monitoring" and outlive a reset, so they are kept
        # and capped instead of being deleted with the run they fired in.
        session.execute(delete(Alert).where(Alert.run_id != run_id, Alert.run_id != "monitoring"))
        session.execute(delete(Incident).where(Incident.run_id != run_id))
        session.execute(delete(Alert).where(
            Alert.id.not_in(select(Alert.id).order_by(Alert.id.desc()).limit(KEEP_ALERTS))
        ))
        session.execute(delete(Incident).where(
            Incident.id.not_in(select(Incident.id).order_by(Incident.id.desc()).limit(KEEP_INCIDENTS))
        ))
        # An execution the simulator has not settled must survive a reset, because the refresh
        # loop still reconciles it against the ledger. Settled rows older than the newest page
        # are unreachable: the decision list reads the most recent KEEP_EXECUTIONS.
        session.execute(delete(Execution).where(
            Execution.status.not_in(OPEN_STATUSES),
            Execution.id.not_in(
                select(Execution.id).where(Execution.status.not_in(OPEN_STATUSES))
                .order_by(Execution.created_at.desc()).limit(KEEP_EXECUTIONS)
            ),
        ))
        # Recommendations referenced only by a pruned execution are now unreachable, so the
        # reference check above can be re-run to collect them. Pending proposals must survive,
        # and so must this run's rejections: the rejection cooldown reads them to avoid
        # regenerating a proposal an operator already turned down. Only approved, expired or
        # foreign-run rows with no surviving execution are orphans.
        session.execute(
            delete(Recommendation).where(
                or_(
                    Recommendation.run_id != run_id,
                    Recommendation.status.not_in(("PROPOSED", "REJECTED")),
                ),
                Recommendation.id.not_in(select(Execution.recommendation_id)),
            )
        )
        session.execute(delete(Recommendation).where(
            Recommendation.run_id == run_id,
            Recommendation.status == "REJECTED",
            Recommendation.id.not_in(
                select(Recommendation.id).where(Recommendation.run_id == run_id,
                                                Recommendation.status == "REJECTED")
                .order_by(Recommendation.created_tick.desc()).limit(KEEP_RECOMMENDATIONS)
            ),
        ))
        # The audit trail is the accountability record, so it outlives a run. It is still
        # bounded, at roughly a year of an operator's activity.
        session.execute(delete(Audit).where(
            Audit.id.not_in(select(Audit.id).order_by(Audit.id.desc()).limit(KEEP_AUDIT))
        ))


def audit(session, actor, action, target, **details):
    session.add(Audit(actor=actor, action=action, target=target, details=details))

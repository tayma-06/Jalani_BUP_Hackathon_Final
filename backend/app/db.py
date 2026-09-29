"""Durable state. SQLite supports local development; Compose uses PostgreSQL."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Float,
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

    def initialize(self):
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            # create_all() only creates missing tables, so indexes that predate an already
            # provisioned database are added here. Both statements are idempotent.
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_executions_created_at ON executions (created_at DESC)"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_demand_obs_run_tick ON demand_obs (run_id, tick DESC)"))
        self.initialized = True

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
        # The audit trail is the accountability record, so it outlives a run. It is still
        # bounded, at roughly a year of an operator's activity.
        session.execute(delete(Audit).where(
            Audit.id.not_in(select(Audit.id).order_by(Audit.id.desc()).limit(KEEP_AUDIT))
        ))


def audit(session, actor, action, target, **details):
    session.add(Audit(actor=actor, action=action, target=target, details=details))

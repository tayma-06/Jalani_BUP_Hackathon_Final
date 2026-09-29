"""Durable state. SQLite supports local development; Compose uses PostgreSQL."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Float, Integer, String, UniqueConstraint, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


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

    def last_snapshot(self):
        with self.session() as session:
            return session.scalar(select(SnapshotRecord).order_by(SnapshotRecord.id.desc()).limit(1))


def audit(session, actor, action, target, **details):
    session.add(Audit(actor=actor, action=action, target=target, details=details))

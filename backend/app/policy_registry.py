"""Versioned policy registry.

A policy configuration is never silently overwritten. Every distinct configuration becomes a
row in `policy_versions` carrying its fuel weights, activation time, the version it replaced,
the actor and the reason. Rollback reactivates a stored configuration rather than trying to
undo the last write, and any recommendation still awaiting review under a different policy is
invalidated so an operator never approves a proposal the active policy would no longer make.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import PolicyVersion, Recommendation, audit, now

log = logging.getLogger("jalani")


class PolicyError(ValueError):
    """A registry operation that must not be applied."""


def row_json(row: PolicyVersion) -> dict:
    return {"version": row.version, "config": row.config, "weights": row.weights,
            "active": row.active, "previous_version": row.previous_version, "actor": row.actor,
            "reason": row.reason, "benchmark": row.benchmark or {}, "activated_at": row.activated_at,
            "run_id": row.run_id}


def active_version(session: Session) -> PolicyVersion | None:
    return session.scalar(select(PolicyVersion).where(PolicyVersion.active.is_(True)).order_by(
        PolicyVersion.activated_at.desc()).limit(1))


def history(session: Session, limit: int = 50) -> list[dict]:
    rows = session.scalars(select(PolicyVersion).order_by(PolicyVersion.activated_at.desc()).limit(limit))
    return [row_json(row) for row in rows]


def register(session: Session, version: str, config: dict, actor: str, reason: str,
             run_id: str | None = None, benchmark: dict | None = None) -> tuple[PolicyVersion, bool]:
    """Create or activate a configuration. Returns the row and whether the active version moved.

    Re-activating the configuration that is already active is a no-op, so a repeated request
    cannot manufacture a new version or a new rollback target.
    """
    current = active_version(session)
    if current is not None and current.config == config:
        return current, False
    previous = current.version if current is not None else None
    if current is not None:
        current.active = False
    row = session.get(PolicyVersion, version)
    if row is None:
        row = PolicyVersion(version=version, config=config, weights=config.get("weights", {}),
                            active=True, previous_version=previous, actor=actor, reason=reason[:300],
                            benchmark=benchmark or {}, run_id=run_id)
        session.add(row)
    else:
        # A version number must describe exactly one configuration, or rollback is ambiguous.
        if row.config != config:
            raise PolicyError(
                f"Policy version {version!r} already exists with a different configuration. "
                "Use a new version name so rollback targets stay unambiguous.")
        row.active, row.previous_version = True, previous
        row.actor, row.reason = actor, reason[:300]
        row.benchmark, row.run_id = benchmark or row.benchmark, run_id or row.run_id
        row.activated_at = now()
    audit(session, actor, "policy.activated", version, previous_version=previous, reason=reason)
    return row, True


def rollback(session: Session, actor: str, reason: str, to_version: str | None = None) -> tuple[PolicyVersion, str]:
    """Restore a previously activated configuration and record who asked for it."""
    current = active_version(session)
    if current is None:
        raise PolicyError("No active policy version to roll back from")
    if to_version is None:
        target = session.get(PolicyVersion, current.previous_version) if current.previous_version else None
        if target is None:
            candidates = [r for r in session.scalars(select(PolicyVersion).order_by(
                PolicyVersion.activated_at.desc())) if r.version != current.version]
            target = candidates[0] if candidates else None
    else:
        target = session.get(PolicyVersion, to_version)
    if target is None:
        raise PolicyError("No earlier policy version is available to restore")
    if target.version == current.version:
        raise PolicyError(f"Policy version {target.version!r} is already active")
    current.active = False
    target.active = True
    target.previous_version = current.version
    target.actor, target.reason, target.activated_at = actor, f"ROLLBACK: {reason}"[:300], now()
    audit(session, actor, "policy.rolled_back", target.version, previous_version=current.version, reason=reason)
    log.info("policy rollback %s -> %s by %s", current.version, target.version, actor)
    return target, current.version


def supersede_stale_recommendations(session: Session, run_id: str, policy_version: str) -> int:
    """Invalidate proposals made under a different policy so none can be approved by mistake."""
    rows = session.scalars(select(Recommendation).where(Recommendation.status == "PROPOSED"))
    changed = 0
    for row in rows:
        if row.run_id != run_id:
            continue
        if row.payload.get("policy_version") == policy_version:
            continue
        row.status = "SUPERSEDED"
        changed += 1
    if changed:
        audit(session, "system", "policy.recommendations_superseded", policy_version, count=changed)
    return changed

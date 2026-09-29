"""Builds /api/v1/status from the service heartbeats, the database and the config."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from replay.api.schemas import (
    ClipperStatusOut,
    CourtStatusOut,
    Level,
    ServiceState,
    ServiceStatusOut,
    SystemStatusOut,
)
from replay.config import AppConfig
from replay.db.repository import Repository
from replay.status import STOPPED, ServiceHeartbeat, read_heartbeat

log = logging.getLogger(__name__)

MONITORED_SERVICES = ("capture", "clipper")
# A clip failure this recent turns the status into a warning
RECENT_FAILURE = timedelta(minutes=10)


def service_state(heartbeat: ServiceHeartbeat | None, now: datetime) -> ServiceState:
    if heartbeat is None:
        return "unknown"
    if heartbeat.state == STOPPED:
        return "stopped"
    if heartbeat.is_stale(now):
        return "down"
    return "running"


def database_ok(repo: Repository) -> bool:
    try:
        repo.list_courts()
        return True
    except Exception:  # noqa: BLE001 - any failure means unavailable
        log.exception("status check: database unavailable")
        return False


def build_status(
    config: AppConfig, repo: Repository, api_started_at: datetime, now: datetime
) -> SystemStatusOut:
    database = database_ok(repo)
    heartbeats = {s: read_heartbeat(config.paths.status_dir, s) for s in MONITORED_SERVICES}
    states = {s: service_state(hb, now) for s, hb in heartbeats.items()}

    services = [
        ServiceStatusOut(
            name="api", state="running", pid=os.getpid(), started_at=api_started_at, updated_at=now
        )
    ]
    for name in MONITORED_SERVICES:
        hb = heartbeats[name]
        services.append(
            ServiceStatusOut(
                name=name,
                state=states[name],
                pid=hb.pid if hb else None,
                started_at=hb.started_at if hb else None,
                updated_at=hb.updated_at if hb else None,
            )
        )

    capture = heartbeats["capture"]
    court_details: dict[str, Any] = (
        capture.details.get("courts", {}) if capture and states["capture"] == "running" else {}
    )
    courts = [
        _court_status(c.id, c.name, court_details.get(c.id), states["capture"], now)
        for c in config.courts
    ]

    clipper_hb = heartbeats["clipper"]
    clipper = (
        ClipperStatusOut.model_validate(clipper_hb.details)
        if clipper_hb and states["clipper"] == "running"
        else None
    )

    level, problems = _evaluate(config, database, services, courts, clipper, now)
    return SystemStatusOut(
        level=level,
        problems=problems,
        checked_at=now,
        database=database,
        services=services,
        courts=courts,
        clipper=clipper,
    )


def _court_status(
    court_id: str,
    name: str,
    details: dict[str, Any] | None,
    capture_service: ServiceState,
    now: datetime,
) -> CourtStatusOut:
    if details is None:
        # Capture not running: the court inherits the service state
        state = "unknown" if capture_service == "running" else capture_service
        return CourtStatusOut(court_id=court_id, name=name, capture=state)
    court = CourtStatusOut.model_validate(
        {
            "court_id": court_id,
            "name": name,
            "capture": details.get("state", "unknown"),
            "since": details.get("since"),
            "last_segment_at": details.get("last_segment_at"),
            "restarts": details.get("restarts", 0),
            "last_error": details.get("last_error"),
            "last_error_at": details.get("last_error_at"),
        }
    )
    if court.last_segment_at is not None:
        court.segment_age_s = round((now - court.last_segment_at).total_seconds(), 1)
    return court


def _evaluate(
    config: AppConfig,
    database: bool,
    services: list[ServiceStatusOut],
    courts: list[CourtStatusOut],
    clipper: ClipperStatusOut | None,
    now: datetime,
) -> tuple[Level, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not database:
        errors.append("database unavailable")
    for service in services:
        if service.state == "down":
            errors.append(f"{service.name} is down (heartbeat lost)")
        elif service.state in ("stopped", "unknown"):
            errors.append(f"{service.name} is not running")
    stall_s = config.capture.stall_timeout_s
    for court in courts:
        if court.capture in ("connecting", "reconnecting", "starting"):
            warnings.append(f"{court.name}: camera {court.capture}")
        elif court.capture == "recording" and (court.segment_age_s or 0) > stall_s:
            warnings.append(f"{court.name}: no new segment for {court.segment_age_s:.0f} s")
    if clipper and clipper.last_error_at and now - clipper.last_error_at < RECENT_FAILURE:
        warnings.append(f"clip failed recently: {clipper.last_error}")
    if errors:
        return "error", errors + warnings
    if warnings:
        return "warning", warnings
    return "ok", []

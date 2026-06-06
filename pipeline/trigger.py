"""
pipeline/trigger.py — PollingTriggerSource.

Implements the TriggerSource protocol. Polls Crustdata for recent leavers
on a per-thesis schedule. Only active when ENABLE_POLLING=true.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models.schemas import (
    EventSource,
    EventSubject,
    EventType,
    NormalisedEvent,
    ThesisConfig,
    TriggerSource,
)


class PollingTriggerSource:
    """Implements TriggerSource protocol. Only polls if ENABLE_POLLING=true."""

    def __init__(self, event_queue: asyncio.Queue, client=None):
        self._queue = event_queue
        self._client = client
        self._scheduler = AsyncIOScheduler()
        self._theses: list[ThesisConfig] = []

    def start(self, theses: list[ThesisConfig]) -> None:
        self._theses = theses
        if os.environ.get("ENABLE_POLLING", "false").lower() != "true":
            # Infrastructure wired, switch is off — do not start scheduler
            return
        for thesis in theses:
            self._scheduler.add_job(
                self._poll,
                "interval",
                hours=thesis.poll_cadence_hours,
                args=[thesis],
                id=f"poll_{thesis.thesis_id}",
            )
        self._scheduler.start()

    def emit_manual(self, event: NormalisedEvent) -> None:
        self._queue.put_nowait(event)

    async def _poll(self, thesis: ThesisConfig) -> None:
        """Run one poll cycle. Only called when ENABLE_POLLING=true."""
        from ingestion.cohort import COHORT_FIELDS, SORTS
        from detect.parse import parse_person
        from detect.leavers import is_leaver

        if not self._client:
            return

        # Build filters: thesis person_filters + recently_changed_jobs=true
        filt = {
            "op": "and",
            "conditions": [
                {"field": "recently_changed_jobs", "type": "=", "value": True},
                *_flatten_conditions(thesis.person_filters),
            ],
        }
        raw = []
        async for page in self._client.person_search(
            filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=50
        ):
            raw.extend(page)
            break  # one page per poll cycle

        people = [parse_person(r) for r in raw]
        for p in people:
            if not is_leaver(p):
                continue
            event = NormalisedEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.person_job_change,
                thesis_id=thesis.thesis_id,
                subject=EventSubject(
                    type="person",
                    profile_url=p.profile_url,
                    name=p.name,
                ),
                payload={"headline": p.headline},
                detected_at=datetime.now(timezone.utc),
                source=EventSource.poll,
            )
            self._queue.put_nowait(event)


def _flatten_conditions(filters: dict) -> list:
    """Extract condition list from a compound filter dict."""
    if not filters:
        return []
    if "conditions" in filters:
        return filters["conditions"]
    if "field" in filters:
        return [filters]
    return []

"""
api/webhooks.py — HMAC-verified Crustdata Watcher webhook receiver.

Normalises the incoming payload into a NormalisedEvent and puts it on the
shared event queue. Never blocks — returns immediately after queuing.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from models.schemas import EventSource, EventSubject, EventType, NormalisedEvent

router = APIRouter()

_event_queue: asyncio.Queue | None = None
_seen_event_ids: OrderedDict = OrderedDict()  # LRU dedup cache
MAX_SEEN = 1000


def set_queue(q: asyncio.Queue) -> None:
    global _event_queue
    _event_queue = q


@router.post("/webhooks/crustdata")
async def crustdata_webhook(request: Request):
    """HMAC-verified Crustdata Watcher webhook. Never blocks."""
    header_name = os.environ.get(
        "CRUSTDATA_WEBHOOK_HEADER", "x-crustdata-signature"
    )
    sig = request.headers.get(header_name, "")
    body = await request.body()

    secret = os.environ.get("CRUSTDATA_WEBHOOK_SECRET", "")
    if secret:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_id = payload.get("event_id") or str(uuid.uuid4())

    # Dedup: ignore replayed events
    if event_id in _seen_event_ids:
        return {"status": "duplicate"}
    _seen_event_ids[event_id] = True
    if len(_seen_event_ids) > MAX_SEEN:
        _seen_event_ids.popitem(last=False)

    event = NormalisedEvent(
        event_id=event_id,
        event_type=EventType(payload.get("event_type", "person_job_change")),
        thesis_id=payload.get("thesis_id", "unknown"),
        subject=EventSubject(
            type=payload.get("subject_type", "person"),
            profile_url=payload.get("profile_url"),
            company_id=payload.get("company_id"),
            name=payload.get("name"),
        ),
        payload=payload,
        detected_at=datetime.now(timezone.utc),
        source=EventSource.watcher,
    )

    if _event_queue is not None:
        _event_queue.put_nowait(event)

    return {"status": "accepted"}

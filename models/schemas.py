"""
Shared type contracts for Stealth Radar v2.
No logic, no I/O — types only.
All cross-track communication uses these types exclusively.
"""
from __future__ import annotations

import typing
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol


# ── Enums ─────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    person_stealth_flip = "person_stealth_flip"
    person_job_change   = "person_job_change"
    company_first_hire  = "company_first_hire"
    funding_announcement = "funding_announcement"
    job_posting         = "job_posting"


class EventSource(str, Enum):
    poll    = "poll"
    watcher = "watcher"


# ── ThesisConfig ──────────────────────────────────────────────────────────────

@dataclass
class AnchorStrategy:
    mode: str                         # "explicit" | "derived"
    companies: list[str] = field(default_factory=list)


@dataclass
class CompanyGate:
    max_headcount: int = 500
    countries: list[str] = field(default_factory=list)   # ISO-3 codes


@dataclass
class ScoringWeights:
    """Seven named floats that must sum to 1.0."""
    size_score:            float = 0.18
    shared_destination:    float = 0.22
    destination_tiny:      float = 0.12
    stealth_founder_ratio: float = 0.16
    window_tightness:      float = 0.12
    co_tenure:             float = 0.14
    open_to:               float = 0.06


@dataclass
class ThesisConfig:
    thesis_id:                str
    label:                    str
    anchor_strategy:          AnchorStrategy
    person_filters:           dict[str, Any]     # compound Crustdata filter schema
    company_gate:             CompanyGate
    scoring_weights:          ScoringWeights
    max_investigation_credits: float = 10.0
    poll_cadence_hours:       int   = 24


# ── Event ─────────────────────────────────────────────────────────────────────

@dataclass
class EventSubject:
    type:        str                   # "person" | "company"
    profile_url: Optional[str] = None
    company_id:  Optional[int] = None
    name:        Optional[str] = None


@dataclass
class NormalisedEvent:
    event_id:    str
    event_type:  EventType
    thesis_id:   str
    subject:     EventSubject
    payload:     dict[str, Any]
    detected_at: datetime
    source:      EventSource


# ── Evidence ──────────────────────────────────────────────────────────────────

@dataclass
class EvidenceItem:
    source:        str           # endpoint or signal name
    finding:       str           # human-readable finding
    supports:      float         # -1.0 (against) to +1.0 (strongly for)
    confidence:    float         # 0.0 to 1.0
    credits_spent: float
    raw:           dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    items:              list[EvidenceItem] = field(default_factory=list)
    total_credits:      float = 0.0
    early_exit_reason:  Optional[str] = None


# ── TriggerSource protocol ────────────────────────────────────────────────────

class TriggerSource(Protocol):
    def start(self, theses: list[ThesisConfig]) -> None: ...
    def emit_manual(self, event: NormalisedEvent) -> None: ...

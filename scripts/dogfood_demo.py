"""
Phase 3 dogfood demo run.

1. Compile thesis from plain-English VC description (Anthropic + free autocomplete)
2. Run one full investigation cycle (live Crustdata APIs)
3. Save dossier to demo/crustdata_icp_dossier.md
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from ingestion.client import CrustdataClient
from pipeline.compiler import compile_thesis
from pipeline.orchestrator import process_event
from models.schemas import (
    NormalisedEvent, EventType, EventSource, EventSubject,
    ThesisConfig
)

DESCRIPTION = (
    "Pre-seed founding teams building AI agents or data infrastructure, "
    "ex-FAANG and top startup alumni such as ex-Stripe, ex-Notion, ex-Figma, "
    "technical cofounders, US-based"
)

DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "demo")
DOSSIER_PATH = os.path.join(DEMO_DIR, "crustdata_icp_dossier.md")


def _get(item, key, default=None):
    """Access dataclass or dict uniformly."""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)

def fmt_evidence(bundle) -> str:
    if not bundle:
        return "_No evidence gathered._"
    items = bundle.get("items") if isinstance(bundle, dict) else getattr(bundle, "items", [])
    if not items:
        return "_No evidence gathered._"
    lines = []
    for item in items:
        s = _get(item, "supports", 0) or 0
        sign = "+" if s > 0 else ("−" if s < 0 else "·")
        lines.append(f"- [{sign}{abs(s):.1f}] **{_get(item,'source','?')}**: {_get(item,'finding','')}")
    return "\n".join(lines)


def write_dossier(thesis: ThesisConfig, results: list[dict]) -> None:
    os.makedirs(DEMO_DIR, exist_ok=True)
    forming = [r for r in results if r["adjudication"].get("label") == "forming_team"]
    all_top = results[:5]

    lines = [
        "# Stealth Radar — Crustdata ICP Demo Dossier",
        "",
        f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "---",
        "",
        "## Thesis",
        "",
        f"> {DESCRIPTION}",
        "",
        f"**Thesis ID:** `{thesis.thesis_id}`  ",
        f"**Label:** {thesis.label}  ",
        f"**Anchor companies:** {', '.join(a['name'] if isinstance(a, dict) else a for a in thesis.anchor_strategy.companies) or '(derived)'}  ",
        f"**Max investigation credits:** {thesis.max_investigation_credits}",
        "",
        "---",
        "",
        f"## Clusters detected: {len(results)} total, {len(forming)} forming-team signals",
        "",
    ]

    for i, r in enumerate(all_top, 1):
        adj   = r.get("adjudication", {})
        dos   = r.get("dossier") or {}
        evb   = r.get("evidence_bundle")
        members = r.get("members", [])
        label = adj.get("label", "unclear")
        badge = {"forming_team": "🟢", "coincidental": "🔵", "layoff_dispersion": "🟡", "unclear": "⚪"}.get(label, "⚪")

        lines += [
            f"### {badge} Cluster #{i} — {r['tier']} (score {r['score']:.1f})",
            "",
            f"**Path:** {r['kind']}  ",
            f"**Destination:** {members[0].get('current_company', '?') if members else '?'}  ",
            f"**Adjudication:** `{label}` (confidence {adj.get('confidence', 0):.0%})  ",
            f"**Rationale:** {adj.get('rationale', '—')}",
            "",
            "**Members:**",
        ]
        for m in members:
            tenure = f" · {m['anchor_tenure']:.0f}mo at anchor" if m.get("anchor_tenure") else ""
            lines.append(f"- {m['name']} — {m.get('headline', '—')}{tenure}")

        if dos:
            lines += [
                "",
                f"**Thesis match:** {dos.get('thesis', '—')}",
                "",
                "**Evidence timeline:**",
            ]
            for ev in (dos.get("evidence_timeline") or []):
                lines.append(f"- {ev}")
            if dos.get("recommended_action"):
                lines += ["", f"**Recommended action:** {dos['recommended_action']}  "]
            if dos.get("urgency"):
                lines.append(f"**Urgency:** `{dos['urgency']}`")

        if evb:
            lines += ["", "**Investigation evidence:**", fmt_evidence(evb)]

        lines += ["", "---", ""]

    lines += [
        "## How this was generated",
        "",
        "1. Plain-English VC thesis compiled to a grounded Crustdata config via Claude + autocomplete",
        "2. Live cohort pull from Crustdata `/person/search` (recently-changed-jobs filter, 50 profiles)",
        "3. Employment-graph clustering (strong + medium paths)",
        "4. Weighted scoring (7 features, hybrid absolute/percentile)",
        "5. Claude adjudication per cluster",
        "6. Investigation loop: destination profile → job postings → web footprint → founder pedigree",
        "7. Predictions logged to tamper-evident git-committed ledger",
        "",
        "_Built on Crustdata + Claude · Aryan Gupta · IIT Bombay_",
    ]

    with open(DOSSIER_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"\n✅ Dossier saved → {DOSSIER_PATH}")


async def main():
    print("=" * 60)
    print("STEALTH RADAR v2 — DOGFOOD DEMO RUN")
    print("=" * 60)

    client = CrustdataClient()
    try:
        # ── Step 1: Compile thesis ────────────────────────────────────────────
        print("\n[1] Compiling thesis via Claude + grounding pass…")
        thesis, explanation = await compile_thesis(DESCRIPTION, client)
        print(f"    Thesis ID:  {thesis.thesis_id}")
        print(f"    Label:      {thesis.label}")
        print(f"    Anchors:    {thesis.anchor_strategy.companies}")
        print(f"    Grounding:  {explanation[:200]}…")

        # ── Step 2: Synthetic trigger event ──────────────────────────────────
        print("\n[2] Emitting synthetic person_stealth_flip event…")
        event = NormalisedEvent(
            event_id    = "demo-evt-001",
            event_type  = EventType.person_stealth_flip,
            thesis_id   = thesis.thesis_id,
            subject     = EventSubject(type="person", name="Demo trigger"),
            payload     = {"description": DESCRIPTION},
            detected_at = datetime.now(timezone.utc),
            source      = EventSource.poll,
        )

        # ── Step 3: Full pipeline run (live credits) ──────────────────────────
        print("\n[3] Running full pipeline (cohort → cluster → investigate → adjudicate)…")
        results = await process_event(
            event,
            thesis,
            client,
            save_predictions=True,
        )

        print(f"\n    Clusters processed: {len(results)}")
        for r in results:
            adj_label = r["adjudication"].get("label", "?")
            has_inv   = "+" if r["evidence_bundle"] else "-"
            has_pred  = r.get("prediction_id") or "none"
            print(f"    [{r['tier']:6}] score={r['score']:.1f}  adj={adj_label}  inv={has_inv}  pred={has_pred}")

        # ── Step 4: Write dossier ─────────────────────────────────────────────
        print("\n[4] Writing dossier…")
        write_dossier(thesis, results)

        # Credit summary
        if os.path.exists("logs/credits.log"):
            with open("logs/credits.log") as f:
                lines = f.readlines()
            demo_lines = [l for l in lines if "orch-" not in l.lower() or "demo" in l.lower()]
            total = sum(float(l.split()[2].replace("cr","")) for l in lines if len(l.split()) >= 3)
            print(f"\n    Credit log: {len(lines)} entries, cumulative total: {total:.2f} cr")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

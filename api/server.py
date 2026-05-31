import hashlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import json
from datetime import date

from sse_starlette.sse import EventSourceResponse

import main
from detect.flow import flow_edges
from detect.leavers import is_leaver
from detect.signals import tag
from detect.parse import parse_person
from ingestion.snapshot import init_db
from config import DUCKDB_PATH

# Resolve paths relative to repo root (parent of this file's directory)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI_DIR = os.path.join(_REPO_ROOT, "ui")

app = FastAPI(title="Stealth Radar")

# CORS: allow only the local dev origins (plus any env override)
_allowed_origin = os.environ.get("ALLOWED_ORIGIN", "")
_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
if _allowed_origin:
    _origins.append(_allowed_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RadarRequest(BaseModel):
    anchor_name: Optional[str] = None
    anchor_linkedin_url: Optional[str] = None


class RadarStreamRequest(BaseModel):
    anchor: Optional[str] = None
    anchor_linkedin_url: Optional[str] = None
    crustdata_key: Optional[str] = None
    anthropic_key: Optional[str] = None


class ValidateKeysRequest(BaseModel):
    crustdata_key: Optional[str] = None
    anthropic_key: Optional[str] = None


class ValidateKeysResponse(BaseModel):
    crustdata: str  # "valid" | "invalid" | "absent"
    anthropic: str


def _cluster_id(cluster) -> str:
    urls = sorted(p.profile_url for p in cluster if p.profile_url)
    return hashlib.md5(",".join(urls).encode()).hexdigest()


def _serialize_member(p) -> dict:
    cur = p.current_role
    return {
        "name": p.name,
        "headline": p.headline,
        "current_title": cur.title if cur else None,
        "current_company": cur.company_name if cur else None,
    }


@app.get("/")
async def serve_spa():
    """Serve the React SPA."""
    index_path = os.path.join(_UI_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="ui/index.html not found")
    return FileResponse(index_path, media_type="text/html")


@app.get("/radar/stream")
async def radar_stream_get(
    anchor: Optional[str] = None,
    anchor_linkedin_url: Optional[str] = None,
):
    """GET SSE endpoint — backward-compatible, uses env-default keys (demo/cached replay)."""
    if not anchor and not anchor_linkedin_url:
        raise HTTPException(status_code=400, detail="Provide anchor or anchor_linkedin_url")

    queue: asyncio.Queue = asyncio.Queue()

    async def run_pipeline():
        try:
            await main.run_with_queue(
                anchor_name=anchor,
                anchor_linkedin_url=anchor_linkedin_url,
                event_queue=queue,
            )
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put({"type": "done"})

    async def event_stream():
        asyncio.create_task(run_pipeline())
        while True:
            event = await queue.get()
            yield {"data": json.dumps(event)}
            if event.get("type") in ("done", "error"):
                break

    return EventSourceResponse(event_stream())


@app.post("/radar/stream")
async def radar_stream_post(req: RadarStreamRequest):
    """POST SSE endpoint — accepts keys in request body (never in URL)."""
    if not req.anchor and not req.anchor_linkedin_url:
        raise HTTPException(status_code=400, detail="Provide anchor or anchor_linkedin_url")

    queue: asyncio.Queue = asyncio.Queue()

    async def run_pipeline():
        try:
            await main.run_with_queue(
                anchor_name=req.anchor,
                anchor_linkedin_url=req.anchor_linkedin_url,
                event_queue=queue,
                crustdata_key=req.crustdata_key or None,
                anthropic_key=req.anthropic_key or None,
            )
        except Exception as e:
            # NEVER log the keys — scrub them from error messages
            safe_msg = str(e)
            await queue.put({"type": "error", "message": safe_msg})
        finally:
            await queue.put({"type": "done"})

    async def event_stream():
        asyncio.create_task(run_pipeline())
        while True:
            event = await queue.get()
            yield {"data": json.dumps(event)}
            if event.get("type") in ("done", "error"):
                break

    return EventSourceResponse(event_stream())


@app.post("/validate-keys", response_model=ValidateKeysResponse)
async def validate_keys(req: ValidateKeysRequest):
    import httpx as _httpx
    from anthropic import Anthropic, AuthenticationError

    crustdata_status = "absent"
    if req.crustdata_key:
        try:
            async with _httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://api.crustdata.com/company/identify",
                    headers={
                        "Authorization": f"Bearer {req.crustdata_key}",
                        "x-api-version": "2025-11-01",
                        "content-type": "application/json",
                    },
                    json={"company_name": "Google"},
                )
            crustdata_status = "valid" if r.status_code < 400 else "invalid"
        except Exception:
            crustdata_status = "invalid"

    anthropic_status = "absent"
    if req.anthropic_key:
        try:
            client = Anthropic(api_key=req.anthropic_key)
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            anthropic_status = "valid"
        except AuthenticationError:
            anthropic_status = "invalid"
        except Exception:
            anthropic_status = "valid"  # other errors (rate limit etc) = key probably valid

    # NEVER echo the key back
    return ValidateKeysResponse(crustdata=crustdata_status, anthropic=anthropic_status)


@app.get("/demo/radar/{anchor}")
async def demo_radar(anchor: str):
    path = os.path.join(_REPO_ROOT, "ui", "demo_cache", f"{anchor.lower()}_radar.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No cached demo for this anchor")
    return json.load(open(path))


@app.post("/radar")
async def radar(req: RadarRequest):
    if not req.anchor_name and not req.anchor_linkedin_url:
        raise HTTPException(status_code=400, detail="Provide anchor_name or anchor_linkedin_url")

    results = await main.run(
        anchor_name=req.anchor_name,
        anchor_linkedin_url=req.anchor_linkedin_url,
    )

    output = []
    for cluster, score, tier_str, features, adjudication, dossier_or_none in results:
        output.append({
            "cluster_id": _cluster_id(cluster),
            "kind": "strong" if features.get("shared_destination") else "medium",
            "score": score,
            "tier": tier_str,
            "members": [_serialize_member(p) for p in cluster],
            "features": features,
            "adjudication": adjudication if isinstance(adjudication, dict) else (adjudication.__dict__ if hasattr(adjudication, '__dict__') else str(adjudication)),
            "dossier": dossier_or_none if isinstance(dossier_or_none, dict) else (dossier_or_none.__dict__ if dossier_or_none and hasattr(dossier_or_none, '__dict__') else dossier_or_none),
        })
    return output


class FlowRequest(BaseModel):
    anchor: Optional[str] = None
    anchor_linkedin_url: Optional[str] = None
    crustdata_key: Optional[str] = None
    anthropic_key: Optional[str] = None


@app.post("/flow")
async def flow(req: FlowRequest):
    """Return TalentFlow edge list for the anchor. Keys come from request body."""
    anchor = req.anchor or req.anchor_linkedin_url or ""
    is_url = anchor.startswith("http")
    results = await main.run(
        anchor_name=None if is_url else anchor,
        anchor_linkedin_url=anchor if is_url else None,
        crustdata_key=req.crustdata_key or None,
        anthropic_key=req.anthropic_key or None,
    )

    seen = set()
    all_leavers = []
    for cluster, *_ in results:
        for p in cluster:
            if p.profile_url not in seen:
                seen.add(p.profile_url)
                all_leavers.append(p)

    if not all_leavers:
        return {"message": "No leavers found. Try a different anchor or check your API key.", "edges": []}

    edges = flow_edges(all_leavers, anchor_label=anchor)
    return {"anchor": anchor, "edges": edges}


@app.get("/backtest")
async def backtest():
    """Return backtest results from DuckDB."""
    try:
        conn = init_db(DUCKDB_PATH)
        rows = conn.execute(
            "SELECT startup, announce_date, horizon_months, caught, score_at_horizon FROM backtest ORDER BY startup, horizon_months"
        ).fetchall()
        conn.close()
    except Exception as e:
        return {"message": f"Could not read backtest table: {e}. Run python -m backtest.evaluate first"}

    if not rows:
        return {"message": "Run python -m backtest.evaluate first"}

    return [
        {
            "startup": r[0],
            "announce_date": str(r[1]),
            "horizon_months": r[2],
            "caught": r[3],
            "score_at_horizon": r[4],
        }
        for r in rows
    ]


@app.get("/backtest/results")
async def backtest_results():
    """Returns full backtest results dict, or {empty: true} if table is empty."""
    try:
        conn = init_db(DUCKDB_PATH)
        rows = conn.execute(
            "SELECT startup, announce_date, horizon_months, caught, score_at_horizon FROM backtest ORDER BY startup, horizon_months"
        ).fetchall()
        conn.close()
    except Exception:
        return {"empty": True}

    if not rows:
        return {"empty": True}

    return {
        "results": [
            {
                "startup": r[0],
                "announce_date": str(r[1]),
                "horizon_months": r[2],
                "caught": bool(r[3]),
                "score_at_horizon": r[4],
            }
            for r in rows
        ]
    }


@app.get("/demo/flow/{anchor}")
async def demo_flow(anchor: str):
    """Serve a pre-generated TalentFlow cache. No API calls, no credits spent."""
    import json
    path = os.path.join(_REPO_ROOT, "ui", "demo_cache", f"{anchor.lower()}_flow.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"No cached flow for anchor '{anchor}'")
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

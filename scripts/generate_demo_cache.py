"""Generate demo cache for OpenAI anchor. Run once, commit the output."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import run_with_queue


async def main():
    queue = asyncio.Queue()
    events = []

    async def drain():
        while True:
            ev = await queue.get()
            events.append(ev)
            if ev.get("type") in ("done", "error"):
                break

    await asyncio.gather(
        run_with_queue(anchor_name="OpenAI", event_queue=queue),
        drain(),
    )
    return events


events = asyncio.run(main())
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "demo_cache"), exist_ok=True)
cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "demo_cache", "openai_radar.json")
with open(cache_path, "w") as f:
    json.dump({"anchor": "OpenAI", "events": events}, f, indent=2, default=str)
print(f"Saved {len(events)} events to {cache_path}")
cluster_count = sum(1 for e in events if e.get("type") == "cluster")
print(f"Clusters: {cluster_count}")

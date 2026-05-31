import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

ADJUDICATE_SYS = (
    "You classify whether a cluster of people leaving a shared employer represents an "
    "intentional team formation or noise. Be skeptical. Name disconfirming evidence. "
    "Respond ONLY with JSON: "
    '{"label":"forming_team|layoff_dispersion|coincidental|unclear",'
    '"confidence":0-1,"rationale":"one sentence"}'
)


def adjudicate(cluster_summary: dict, anthropic_key: str | None = None) -> dict:
    client = Anthropic(api_key=anthropic_key or ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        system=[
            {
                "type": "text",
                "text": ADJUDICATE_SYS,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": json.dumps(cluster_summary)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(
            text.strip().removeprefix("```json").removesuffix("```").strip()
        )
    except json.JSONDecodeError:
        return {"label": "unclear", "confidence": 0.0, "rationale": "parse_error"}

import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

DOSSIER_SYS = (
    "You are a deal/talent scout. From structured cluster data, write a tight brief. "
    "Name exact signals and dates; no filler; flag anything that weakens the thesis. "
    'Respond ONLY with JSON: {"summary":str,"members":[str],'
    '"evidence_timeline":[str],"thesis":str,"recommended_action":str,'
    '"urgency":"now|30d|90d"}'
)


def dossier(cluster_summary: dict, anthropic_key: str | None = None) -> dict:
    _key = anthropic_key or ANTHROPIC_API_KEY
    if not _key:
        raise ValueError("Anthropic API key is required. Enter it in the sidebar.")
    client = Anthropic(api_key=_key)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=[
            {
                "type": "text",
                "text": DOSSIER_SYS,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": json.dumps(cluster_summary)}],
    )
    text  = "".join(b.text for b in msg.content if b.type == "text")
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {
        "summary": "parse_error",
        "members": [],
        "evidence_timeline": [],
        "thesis": "",
        "recommended_action": "",
        "urgency": "90d",
    }

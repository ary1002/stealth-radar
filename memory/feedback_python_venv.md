---
name: feedback-python-venv
description: Always use .venv Python for this project, not system python3
metadata:
  type: feedback
---

Always run Python commands using `.venv/bin/python` (not `python3` or `python`).

**Why:** The project has a `.venv` at `/home/aryan/Desktop/stealth-radar/.venv/` with all dependencies installed. System python3 lacks the project's packages.

**How to apply:** In every Bash tool call that runs Python, prefix with `.venv/bin/python` or `cd` to the project root and use `.venv/bin/python`.

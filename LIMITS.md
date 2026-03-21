# Token & Context Limits — Testing vs Production

**Status: PRODUCTION values restored** (2026-03-22). Watcher reduced from 12→8 iterations.

## Cycle System (orchestrator)

| Component | File | Production | Testing |
|---|---|---|---|
| Watcher max iterations | `orchestrator/config.py` | 8 | 5 |
| Agent `max_tokens` default | `orchestrator/agent_runner.py` | 4096 | 1024 |
| Reporter `max_tokens` (override) | `orchestrator/config.py` | 4096 | 4096 (kept full — writes report with URLs) |
| `web_fetch` content | `orchestrator/tools.py` | 8000 chars | 2000 chars |
| `web_search` snippet | `orchestrator/tools.py` | 500 chars | 200 chars |
| Input data files (`MAX_PER_FILE`) | `orchestrator/main.py` | 8000 chars | 1500 chars |
| State JSON in prompt | `orchestrator/main.py` | unlimited | 2000 chars |

## Case Bots (api/bots.py)

| Component | Production | Testing |
|---|---|---|
| Bot A org profile context | 3000 chars | 500 chars |
| Bot A donor registry context | 3000 chars | 500 chars |
| Bot A donor docs context | 3000 chars | 500 chars |
| Bot A `max_tokens` | 1024 | 512 |
| Bot B doc text input | 15000 chars | 3000 chars |
| Bot B `max_tokens` | 4096 | 2048 |
| Bot C org profile context | 3000 chars | 500 chars |
| Bot C grant brief context | 3000 chars | 500 chars |
| Bot C donor registry context | 2000 chars | 500 chars |
| Bot C org docs context | 3000 chars | 500 chars |
| Bot C `max_tokens` | 8192 | 2048 |
| Bot D grant brief context | 2000 chars | 500 chars |
| Bot D gap analysis `max_tokens` | 800 | 400 |
| Bot D answer `max_tokens` | 2048 | 512 |
| File ingest doc text | 10000 chars | 2000 chars |
| File ingest `max_tokens` | 4096 | 1024 |

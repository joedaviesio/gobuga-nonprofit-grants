"""CLI entry point."""

import tirith  # noqa: F401  — must come before anthropic; routes calls through local tirith proxy

import sys
from orchestrator.main import run_cycle
from orchestrator.signoff import interactive_signoff


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 -m orchestrator run [YYYY-MM-DD]    Run a grant scanning cycle")
        print("  python3 -m orchestrator signoff [YYYY-MM-DD] Record decisions on today's report")
        print("  python3 -m orchestrator serve [--port PORT]  Start the case API server")
        sys.exit(1)

    command = sys.argv[1]
    date_arg = sys.argv[2] if len(sys.argv) > 2 else None

    if command == "run":
        run_cycle(date_arg)
    elif command == "signoff":
        interactive_signoff(date_arg)
    elif command == "serve":
        port = 8102
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        import uvicorn
        uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=True)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

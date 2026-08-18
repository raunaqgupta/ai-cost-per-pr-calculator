#!/usr/bin/env python3
"""SessionStart hook: injects the generic issue-driven workflow pipeline into context."""
import json
from pathlib import Path

PIPELINE_PATH = Path(__file__).parent / "pipeline.md"


def main():
    text = PIPELINE_PATH.read_text()
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "SessionStart"},
        "additionalContext": text,
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import sys
        print(f"inject_pipeline: unexpected error: {e}", file=sys.stderr)
        sys.exit(0)

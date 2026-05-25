#!/usr/bin/env python3
"""SessionStart hook: check for pending relay messages.

Scans ~/.mcloop/pending/ for message files and reports a summary
so the user knows there are messages waiting.
"""

import json
import os
from pathlib import Path

PENDING_DIR = Path.home() / ".mcloop" / "pending"


def main() -> None:
    if not PENDING_DIR.is_dir():
        return

    messages = []
    for name in sorted(os.listdir(PENDING_DIR)):
        fpath = PENDING_DIR / name
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text().strip()
            if text:
                messages.append({"file": name, "preview": text[:100]})
        except OSError:
            continue

    if not messages:
        return

    # Output as JSON so Claude Code can parse and display it.
    print(
        json.dumps(
            {
                "pendingMessages": len(messages),
                "messages": messages,
            }
        )
    )


if __name__ == "__main__":
    main()

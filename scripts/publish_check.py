#!/usr/bin/env python3
"""Decide from `pac org fetch` output whether an agent's last publish succeeded after a given time.

Usage: pac org fetch --xml "<fetch>...synchronizationstatus...</fetch>" | publish_check.py --since 2026-11-23T05:22:00Z

Why this exists: `pac copilot publish` 2.16.1 can fail with "Invalid response format" while polling the
publish status (seen for a harness agent published by an app identity), even though the publish ran and
succeeded. The bot record carries the truth: synchronizationstatus.lastFinishedPublishOperation. Exit 0
only when it says `Succeeded` and started no earlier than --since (minus a clock-skew allowance).
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

SKEW = timedelta(seconds=60)


def parse_time(text: str) -> datetime:
    text = text.strip().replace("Z", "+00:00")
    # .NET writes 7 fractional digits; Python accepts at most 6.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def last_publish(fetch_output: str) -> dict | None:
    """The lastFinishedPublishOperation object inside the fetched synchronizationstatus JSON, if any."""
    start, end = fetch_output.find("{"), fetch_output.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        status = json.loads(fetch_output[start:end + 1])
    except json.JSONDecodeError:
        return None
    op = status.get("lastFinishedPublishOperation")
    return op if isinstance(op, dict) else None


def succeeded_since(fetch_output: str, since: datetime) -> bool:
    op = last_publish(fetch_output)
    if not op or op.get("status") != "Succeeded" or not op.get("operationStart"):
        return False
    return parse_time(op["operationStart"]) >= since - SKEW


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True, help="UTC time the publish was requested, ISO 8601")
    args = parser.parse_args()
    ok = succeeded_since(sys.stdin.read(), parse_time(args.since))
    print("publish verified from the bot record" if ok else "no successful publish found since the request",
          file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

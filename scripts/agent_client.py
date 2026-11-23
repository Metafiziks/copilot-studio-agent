#!/usr/bin/env python3
"""Call the deployed Copilot Studio agent as a signed-in user.

Uses Microsoft's Agents SDK (`microsoft-agents-copilotstudio-client`) with an MSAL public-client token
for the Power Platform API. The agent retrieves SharePoint content as the signed-in user, so a real
user token is required; an app-only token has no user for SharePoint to trim by.

Setup: python3 -m venv .venv-eval && .venv-eval/bin/pip install microsoft-agents-copilotstudio-client msal
Config (environment or .env): EVAL_CLIENT_ID, EVAL_TENANT_ID (from scripts/setup_entra_app.sh),
AGENT_ENVIRONMENT_ID, AGENT_SCHEMA_NAME (Copilot Studio > Settings > Advanced > Metadata).

Probe one question and print the raw activities (useful to see how citations are shaped):
  .venv-eval/bin/python scripts/agent_client.py probe "What is the lockout tagout procedure?" [--auth device|interactive]

Tokens are cached in .build/token_cache.json (gitignored; it holds refresh tokens, so keep it private).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = "https://api.powerplatform.com/.default"
TOKEN_CACHE = ROOT / ".build" / "token_cache.json"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"Missing configuration: {', '.join(missing)} (set in .env; see .env.example)")
    return {n: os.environ[n] for n in names}


def acquire_token(client_id: str, tenant_id: str, mode: str = "device") -> str:
    """Silent from cache if possible, else device-code or interactive-browser sign-in."""
    import msal

    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE.exists():
        cache.deserialize(TOKEN_CACHE.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant_id}", token_cache=cache
    )
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent([SCOPE], account=accounts[0])
    if not result:
        if mode == "device":
            flow = app.initiate_device_flow(scopes=[SCOPE])
            if "message" not in flow:
                raise SystemExit(f"Could not start device sign-in: {flow}")
            print(flow["message"], file=sys.stderr, flush=True)
            result = app.acquire_token_by_device_flow(flow)
        else:
            result = app.acquire_token_interactive([SCOPE])
    if cache.has_state_changed:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(cache.serialize(), encoding="utf-8")
        TOKEN_CACHE.chmod(0o600)
    if not result or "access_token" not in result:
        raise SystemExit(f"Sign-in failed: {(result or {}).get('error_description', result)}")
    return result["access_token"]


def make_client(mode: str = "device"):
    from microsoft_agents.copilotstudio.client import ConnectionSettings, CopilotClient

    cfg = require_env("EVAL_CLIENT_ID", "EVAL_TENANT_ID", "AGENT_ENVIRONMENT_ID", "AGENT_SCHEMA_NAME")
    settings = ConnectionSettings(
        environment_id=cfg["AGENT_ENVIRONMENT_ID"],
        agent_identifier=cfg["AGENT_SCHEMA_NAME"],
        cloud=None,
        copilot_agent_type=None,
        custom_power_platform_cloud=None,
    )
    token = acquire_token(cfg["EVAL_CLIENT_ID"], cfg["EVAL_TENANT_ID"], mode)
    return CopilotClient(settings, token)


@dataclass
class Reply:
    question: str
    text: str = ""
    activities: list = field(default_factory=list)  # message-type activities as plain dicts
    latency_ms: float = 0.0
    error: str | None = None


def activity_to_dict(activity) -> dict:
    return activity.model_dump(mode="json", by_alias=True, exclude_none=True)


async def ask(client, question: str, timeout: float = 120.0) -> Reply:
    """Ask one question in a fresh conversation and collect the agent's messages."""
    reply = Reply(question=question)
    started = time.perf_counter()

    async def run() -> None:
        conversation_id = None
        async for activity in client.start_conversation(True):
            if conversation_id is None and getattr(activity, "conversation", None):
                conversation_id = activity.conversation.id
        if not conversation_id:
            raise RuntimeError("no conversation id returned")
        async for activity in client.ask_question(question, conversation_id):
            data = activity_to_dict(activity)
            if data.get("type") == "message" and data.get("text"):
                reply.activities.append(data)

    try:
        await asyncio.wait_for(run(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - any failure is recorded, never counted as a pass
        reply.error = f"{type(exc).__name__}: {exc}"
    reply.latency_ms = (time.perf_counter() - started) * 1000
    reply.text = "\n".join(a["text"] for a in reply.activities)
    return reply


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe", help="ask one question and print the raw activities")
    probe.add_argument("question")
    probe.add_argument("--auth", choices=["device", "interactive"], default="device")
    args = parser.parse_args()
    load_dotenv()
    client = make_client(args.auth)
    reply = asyncio.run(ask(client, args.question))
    print(json.dumps({"question": reply.question, "latency_ms": round(reply.latency_ms),
                      "error": reply.error, "text": reply.text, "activities": reply.activities}, indent=2))


if __name__ == "__main__":
    main()

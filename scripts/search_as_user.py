#!/usr/bin/env python3
"""Ask Microsoft Search what a given user can find, the way the agent's SharePoint search sees it.

Usage: .venv-eval/bin/python scripts/search_as_user.py "lockout" "conveyor" [--site https://host/sites/nick]

Why: the agent searches SharePoint AS the signed-in user, and a browser check as a different (or an
unlicensed) user tells you nothing. This signs in as ONE user (device code, with the project's app
registration, delegated Sites.Read.All; the user consents for themselves) and prints how many documents
each query returns for that user and where they live. Use AGENT_TOKEN_CACHE to keep one sign-in per
user; the default cache here is .build/token_cache_graph.json so the agent's own cache is untouched.
Config: EVAL_CLIENT_ID, EVAL_TENANT_ID (from .env).
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = "https://graph.microsoft.com/Sites.Read.All"
CACHE = Path(os.environ.get("GRAPH_TOKEN_CACHE") or ROOT / ".build" / "token_cache_graph.json")


def token(client_id: str, tenant_id: str) -> str:
    import msal

    cache = msal.SerializableTokenCache()
    if CACHE.exists():
        cache.deserialize(CACHE.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant_id}",
                                       token_cache=cache)
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent([SCOPE], account=accounts[0])
    if not result:
        flow = app.initiate_device_flow(scopes=[SCOPE])
        print(flow["message"], file=sys.stderr, flush=True)
        result = app.acquire_token_by_device_flow(flow)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(cache.serialize(), encoding="utf-8")
        CACHE.chmod(0o600)
    if "access_token" not in result:
        sys.exit(f"Sign-in failed: {result.get('error_description', result)}")
    return result["access_token"]


def search(access: str, query: str, size: int = 25) -> list[dict]:
    body = json.dumps({"requests": [{"entityTypes": ["driveItem"], "query": {"queryString": query}, "from": 0,
                                     "size": size}]}).encode()
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/search/query", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    hits = []
    for container in data["value"][0].get("hitsContainers", []):
        hits += container.get("hits", [])
    return [{"name": h["resource"].get("name"), "url": h["resource"].get("webUrl")} for h in hits]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("queries", nargs="+")
    parser.add_argument("--site", help="only count hits whose URL starts with this site URL")
    args = parser.parse_args()
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    access = token(os.environ["EVAL_CLIENT_ID"], os.environ["EVAL_TENANT_ID"])
    for q in args.queries:
        try:
            hits = search(access, q)
        except urllib.error.HTTPError as exc:
            print(f"{q!r}: HTTP {exc.code} {exc.read().decode()[:300]}")
            continue
        if args.site:
            hits = [h for h in hits if (h["url"] or "").lower().startswith(args.site.lower())]
        print(f"{q!r}: {len(hits)} hit(s)")
        for h in hits[:8]:
            print(f"   {h['name']}  {h['url']}")


if __name__ == "__main__":
    main()

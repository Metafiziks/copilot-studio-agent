#!/usr/bin/env python3
"""Create, fill and delete SharePoint sites through Microsoft Graph.

Standard library only; tokens come from the Azure CLI (`az login` for the target tenant first).
A site is created as a private Microsoft 365 group, which provisions a SharePoint team site (and a
mailbox). SharePoint REST returned 401 for an Azure CLI token in testing, so Graph is used throughout.

Subcommands (JSON on stdout, progress on stderr):
  ensure-site  --nick N --title T [--description D] [--member UPN ...] [--public]
  upload       --nick N --dir DIR
  status       --nick N
  delete-site  --nick N [--purge]

Newly created sites take roughly 20-30 minutes before SharePoint search (and therefore the agent)
can find their content.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPH = "https://graph.microsoft.com/v1.0"
MAX_SIMPLE_UPLOAD = 4 * 1024 * 1024  # Graph simple-upload limit; larger files need an upload session
RETRY_STATUSES = (429, 502, 503, 504)


class GraphError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Graph HTTP {status}: {message}")
        self.status = status


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def az_token() -> str:
    proc = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://graph.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True,
    )
    token = proc.stdout.strip()
    if proc.returncode or not token:
        raise SystemExit("az could not issue a Graph token. Run `az login` for the target tenant.")
    return token


# --- pure helpers (unit-tested) -------------------------------------------------------------

def group_payload(title: str, nick: str, description: str = "", visibility: str = "Private") -> dict:
    if visibility not in ("Private", "Public"):
        raise ValueError("visibility must be Private or Public")
    payload = {
        "displayName": title,
        "mailNickname": nick,
        "groupTypes": ["Unified"],
        "mailEnabled": True,
        "securityEnabled": False,
        "visibility": visibility,
    }
    if description:
        # Graph rejects an empty description ("Invalid value specified for property 'description'"),
        # so the field is only sent when there is text.
        payload["description"] = description
    return payload


def site_web_url(host: str, nick: str) -> str:
    return f"https://{host}/sites/{nick}"


def remote_path(root: Path, file: Path) -> str:
    """Path of `file` inside the library, relative to `root`, with forward slashes."""
    return file.relative_to(root).as_posix()


def iter_files(root: Path, suffixes: tuple[str, ...] = (".docx", ".pdf", ".pptx", ".xlsx")) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def quote_path(path: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))


# --- Graph client ---------------------------------------------------------------------------

class Graph:
    def __init__(self, token_provider=az_token, sleep=time.sleep):
        self._token_provider = token_provider
        self._sleep = sleep
        self._token = None

    def _headers(self, content_type: str) -> dict:
        if self._token is None:
            self._token = self._token_provider()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": content_type}

    def _open(self, req: urllib.request.Request) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def request(self, method: str, path: str, body: dict | None = None, data: bytes | None = None,
                content_type: str = "application/json", allow: tuple[int, ...] = ()) -> dict | None:
        url = path if path.startswith("https://") else GRAPH + path
        payload = json.dumps(body).encode() if body is not None else data
        for attempt in range(4):
            req = urllib.request.Request(url, data=payload, method=method, headers=self._headers(content_type))
            status, raw = self._open(req)
            if status in RETRY_STATUSES and attempt < 3:
                wait = 5 * (2 ** attempt)
                log(f"  Graph {status}, retrying in {wait}s ...")
                self._sleep(wait)
                continue
            if status in allow:
                return None
            if 200 <= status < 300:
                return json.loads(raw) if raw else {}
            try:
                message = json.loads(raw).get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                message = raw[:200].decode("utf-8", "replace")
            raise GraphError(status, message)
        raise GraphError(0, "retries exhausted")  # pragma: no cover

    # --- operations ---

    def find_group(self, nick: str) -> str | None:
        flt = urllib.parse.quote(f"mailNickname eq '{nick}'")
        result = self.request("GET", f"/groups?$filter={flt}&$select=id") or {}
        groups = result.get("value", [])
        return groups[0]["id"] if groups else None

    def ensure_group(self, title: str, nick: str, description: str = "", visibility: str = "Private") -> tuple[str, bool]:
        existing = self.find_group(nick)
        if existing:
            return existing, False
        created = self.request("POST", "/groups", body=group_payload(title, nick, description, visibility))
        return created["id"], True

    def add_member(self, group_id: str, upn: str) -> bool:
        user = self.request("GET", f"/users/{urllib.parse.quote(upn)}?$select=id")
        try:
            self.request("POST", f"/groups/{group_id}/members/$ref",
                         body={"@odata.id": f"{GRAPH}/directoryObjects/{user['id']}"})
            return True
        except GraphError as exc:
            if exc.status == 400 and "already exist" in str(exc).lower():
                return False
            raise

    def wait_for_site(self, group_id: str, timeout: int = 900, interval: int = 10) -> str:
        deadline = time.time() + timeout
        while True:
            site = self.request("GET", f"/groups/{group_id}/sites/root?$select=webUrl", allow=(404,))
            if site and site.get("webUrl"):
                return site["webUrl"]
            if time.time() > deadline:
                raise GraphError(0, "timed out waiting for the SharePoint site to provision")
            self._sleep(interval)

    def ensure_site(self, nick: str, title: str, description: str = "", members: list[str] | None = None,
                    visibility: str = "Private") -> dict:
        group_id, created = self.ensure_group(title, nick, description, visibility)
        log(f"{'created' if created else 'found'} group {nick} ({group_id})")
        added = [m for m in (members or []) if self.add_member(group_id, m)]
        url = self.wait_for_site(group_id)
        log(f"site ready: {url}")
        return {"group_id": group_id, "web_url": url, "created": created, "members_added": added}

    def upload_file(self, group_id: str, local: Path, remote: str) -> None:
        size = local.stat().st_size
        if size > MAX_SIMPLE_UPLOAD:
            raise ValueError(f"{local} is {size} bytes; files over {MAX_SIMPLE_UPLOAD} need an upload session")
        self.request("PUT", f"/groups/{group_id}/drive/root:/{quote_path(remote)}:/content",
                     data=local.read_bytes(), content_type="application/octet-stream")

    def upload_tree(self, group_id: str, root: Path) -> list[str]:
        uploaded = []
        for file in iter_files(root):
            remote = remote_path(root, file)
            self.upload_file(group_id, file, remote)
            log(f"  uploaded {remote}")
            uploaded.append(remote)
        return uploaded

    def delete_group(self, nick: str, purge: bool = False) -> bool:
        """Delete the group (and its site). Entra soft-deletes it for 30 days and reserves the mail
        nickname meanwhile; `purge` also removes the soft-deleted copy so the name can be reused."""
        group_id = self.find_group(nick)
        if not group_id:
            return False
        self.request("DELETE", f"/groups/{group_id}")
        if purge:
            self.request("DELETE", f"/directory/deletedItems/{group_id}", allow=(404,))
        return True


# --- CLI --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None, graph: Graph | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ensure-site")
    p.add_argument("--nick", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--member", action="append", default=[])
    p.add_argument("--public", action="store_true")
    p = sub.add_parser("upload")
    p.add_argument("--nick", required=True)
    p.add_argument("--dir", type=Path, required=True)
    sub.add_parser("status").add_argument("--nick", required=True)
    p = sub.add_parser("delete-site")
    p.add_argument("--nick", required=True)
    p.add_argument("--purge", action="store_true", help="also purge the soft-deleted group so the nickname is reusable")
    args = parser.parse_args(argv)
    graph = graph or Graph()

    if args.command == "ensure-site":
        result = graph.ensure_site(args.nick, args.title, args.description, args.member,
                                   "Public" if args.public else "Private")
    elif args.command == "upload":
        group_id = graph.find_group(args.nick)
        if not group_id:
            raise SystemExit(f"No group with mail nickname {args.nick!r}; run ensure-site first")
        result = {"group_id": group_id, "uploaded": graph.upload_tree(group_id, args.dir)}
    elif args.command == "status":
        group_id = graph.find_group(args.nick)
        result = {"exists": bool(group_id), "group_id": group_id}
    else:
        result = {"deleted": graph.delete_group(args.nick, purge=args.purge)}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Capture a claude.ai web conversation as raw JSON, for provenance.

claude.ai is a client-rendered SPA behind Cloudflare, so the transcript can
only be reached through an authenticated browser. This drives a debug Chrome
over the DevTools Protocol and runs a `fetch()` *inside the page context*
(cookies + origin come for free) against the conversation API.

Prerequisites (see the Quirework workflow guide):
  1. Chrome 136+ ignores --remote-debugging-port on the default profile, so
     launch a SEPARATE profile dir:
       google-chrome --user-data-dir=$HOME/tmp/cdp-chrome-profile \\
         --remote-debugging-port=9222 \\
         --remote-allow-origins=http://127.0.0.1:9222 \\
         --no-first-run --no-default-browser-check https://claude.ai/login
  2. Log in to claude.ai in that window.
  3. python fetch-conversation.py <conversation-uuid>

Writes .quirework/provenance/conversations/<uuid>.raw.json (verbatim API response).
"""

import json, sys, urllib.request
import uuid as uuid_module
from quirework.project import current_project

# Outputs use the selected project configuration, independently of where
# this package is installed.

PORT = 9222


def http_json(path, port=9222):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as r:
        return json.load(r)


def page_target(port=9222):
    for t in http_json("/json/list", port):
        if t.get("type") == "page" and "claude.ai" in (t.get("url") or ""):
            return t
    raise SystemExit("no claude.ai page target — is the debug Chrome logged in?")


class CDP:
    def __init__(self, url):
        import websocket

        self.ws = websocket.create_connection(url, max_size=None)
        self.i = 0

    def call(self, method, timeout=60, **params):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        self.ws.settimeout(timeout)
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]


JS = r"""
(async () => {
  const orgs = await (await fetch("/api/organizations",
                {headers:{accept:"application/json"}})).json();
  const oid = orgs[0].uuid;
  const url = `/api/organizations/${oid}/chat_conversations/%s`
            + `?tree=True&rendering_mode=messages&render_all_tools=true&consistency=strong`;
  const r = await fetch(url, {headers:{accept:"application/json"}});
  return JSON.stringify({status: r.status, org: oid, body: await r.text()});
})()
"""

USAGE = """usage: quirework provenance capture CONVERSATION_UUID

Capture a claude.ai conversation into the provenance corpus, writing
.quirework/provenance/conversations/<uuid>.raw.json

Needs the `capture` extra (websocket-client) and a Chrome running with remote
debugging, logged in to claude.ai. The uuid is the last path segment of the
conversation URL.

options:
  -h, --help  show this help message and exit
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE, end="")
        return 0
    if len(argv) != 1:
        raise SystemExit(USAGE.rstrip("\n"))
    return capture(argv[0])


def capture(conversation, *, port=9222):
    uuid = str(uuid_module.UUID(conversation))
    directory = current_project().path("provenance.conversations")
    directory.mkdir(parents=True, exist_ok=True)
    out = str(directory / f"{uuid}.raw.json")
    cdp = CDP(page_target(port)["webSocketDebuggerUrl"])
    res = cdp.call(
        "Runtime.evaluate", expression=JS % uuid, awaitPromise=True, returnByValue=True
    )
    val = res.get("result", {}).get("value")
    if val is None:
        raise SystemExit(f"no value returned: {json.dumps(res)[:500]}")
    payload = json.loads(val)
    if payload["status"] != 200:
        raise SystemExit(f"HTTP {payload['status']}: {payload['body'][:300]}")
    with open(out, "w") as f:
        f.write(payload["body"])
    conv = json.loads(payload["body"])
    print(
        f"wrote {out}: {len(payload['body'])} bytes, "
        f"{len(conv.get('chat_messages', []))} messages, "
        f"name={conv.get('name')!r}"
    )


if __name__ == "__main__":
    main()

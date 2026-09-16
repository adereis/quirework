#!/usr/bin/env python3
"""Capture a gemini.google.com conversation by scraping the rendered DOM.

Unlike claude.ai, Gemini has no clean conversation JSON API (it loads chats via
obfuscated batchexecute RPCs), so we drive the authenticated debug Chrome to the
conversation and read the rendered turns out of the DOM.

  usage: fetch-gemini.py <conversation-id-or-url>
  writes private/provenance/conversations/gemini-<id>.raw.html  (verbatim turn DOM)
         private/provenance/conversations/gemini-<id>.md         (rendered transcript)

Note: the Gemini DOM exposes no per-message timestamps, so the transcript has no
dates — annotate the index row manually.
"""

import json, sys, time, urllib.request, datetime, re
from pathlib import Path

# Outputs use the selected project configuration, independently of where
# this package is installed.
from quirework.project import current_project

PORT = 9222


def hj(p):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{p}"))


def page_target():
    for t in hj("/json/list"):
        if t.get("type") == "page":
            return t
    raise SystemExit("no page target")


class CDP:
    def __init__(self, url):
        import websocket

        self.ws = websocket.create_connection(url, max_size=None)
        self.i = 0

    def call(self, method, timeout=30, **p):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": p}))
        self.ws.settimeout(timeout)
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result")

    def ev(self, expr, timeout=30):
        r = self.call(
            "Runtime.evaluate", timeout=timeout, expression=expr, returnByValue=True
        )
        return r["result"].get("value")


EXTRACT = r"""
JSON.stringify((() => {
  const out = [], htmls = [];
  document.querySelectorAll('.conversation-container').forEach(c => {
    const u = c.querySelector('.query-text, user-query-content');
    const m = c.querySelector('message-content, .model-response-text, .markdown');
    out.push({
      user:  u ? u.innerText.replace(/^You said\s*/, '').trim() : null,
      model: m ? m.innerText.trim() : null,
    });
    htmls.push(c.outerHTML);
  });
  let model = null;
  for (const s of ['.logo-pill-label-container','[data-test-id="bard-mode-menu-button"]',
                   '.current-mode-title']) {
    const e = document.querySelector(s);
    if (e && e.innerText.trim()) { model = e.innerText.trim().replace(/\s+/g,' '); break; }
  }
  return {title: document.title, url: location.href, model, turns: out, htmls};
})())
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        raise SystemExit("usage: fetch-gemini.py <conversation-id-or-url>")
    arg = argv[0]
    cid = arg.rstrip("/").split("/")[-1]
    url = arg if arg.startswith("http") else f"https://gemini.google.com/app/{cid}"

    cdp = CDP(page_target()["webSocketDebuggerUrl"])
    cdp.call("Page.enable")
    cdp.call("Page.navigate", url=url)
    # wait for conversation turns to render (and settle)
    last, stable = -1, 0
    for _ in range(30):
        time.sleep(1)
        n = cdp.ev("document.querySelectorAll('.conversation-container').length") or 0
        if n and n == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last = n
    data = json.loads(cdp.ev(EXTRACT))
    turns = data["turns"]
    if not turns:
        raise SystemExit(
            "no conversation turns found — is the chat loaded / logged in?"
        )

    raw = str(
        current_project().path("provenance.conversations") / f"gemini-{cid}.raw.html"
    )
    with open(raw, "w") as f:
        f.write(
            f"<!-- gemini conversation {cid} captured {datetime.date.today()} "
            f"from {data['url']} -->\n" + "\n\n".join(data["htmls"])
        )

    md = str(current_project().path("provenance.conversations") / f"gemini-{cid}.md")
    o = [
        f"# {data['title'] or 'Gemini conversation'}\n",
        "> **Provenance artifact — DOM scrape of a gemini.google.com conversation.**\n>\n"
        f"> Gemini exposes no conversation JSON, so this is the rendered transcript;\n"
        f"> the verbatim turn DOM is in [`gemini-{cid}.raw.html`](./gemini-{cid}.raw.html).\n",
        "\n| | |\n|---|---|",
        f"| Source | {data['url']} (private) |",
        f"| Platform | Google Gemini{(' · ' + data['model']) if data['model'] else ''} |",
        f"| Turns | {len(turns)} exchanges |",
        "| Dates | not exposed by Gemini DOM (annotate manually) |",
        f"| Captured | {datetime.date.today().isoformat()} (authenticated CDP DOM scrape) |\n",
        "\n---\n",
    ]
    for k, t in enumerate(turns, 1):
        if t["user"] is not None:
            o.append(f"\n## You · #{k}\n\n{t['user']}\n")
        if t["model"] is not None:
            o.append(f"\n## Gemini · #{k}\n\n{t['model']}\n")
    open(md, "w").write("\n".join(o).rstrip() + "\n")
    print(f"wrote {raw} and {md}: {len(turns)} exchanges, model={data['model']!r}")


if __name__ == "__main__":
    main()

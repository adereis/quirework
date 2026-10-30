"""A seat's whole reply, and any text its harness wrote into the turn.

Claude Code ends a model response that reaches the model's output ceiling,
writes a message of its own asking the model to resume, and lets the model
finish in a further response. Its result row carries only that last
response. An answer read from the result alone therefore loses everything
the seat wrote before the cut, and nothing about it looks wrong: the turn
succeeded, and the answer is merely short for what it cost.

The turn's stream holds every response and every message the harness
injected, which it marks ``isSynthetic``. The native session says why each
response stopped. Together they rebuild the reply: each response that
stopped at the ceiling and was resumed is joined, verbatim, to the one
that resumed it. The model is told to pick up mid-thought, and does, so
the join adds nothing between the parts, and a cut that fell inside a word
comes back as that word.

The harness's own message never enters the answer. It is returned as
harness text, with the place in the answer where it was said, so a reader
can tell the seat's words from the tool's.
"""

LIMIT = "max_tokens"


def text_of(content):
    """The text in a message's content, which is a string or a list of blocks."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content or []
        if isinstance(block, dict) and block.get("type") == "text"
    )


def claude(stream, native):
    """``(answer, evidence)`` for one Claude turn.

    ``stream`` is the turn's own stream-json rows, with exactly one result
    row, which the caller has already accepted. ``native`` is any native
    session rows that include the turn's responses. ``evidence`` has two
    keys. ``output_limit`` is ``None`` when the native session states no
    stop reason for any of the turn's responses, so the record cannot say
    whether the ceiling was reached; otherwise it says how many responses
    reached it, where the answer was joined, and whether the answer itself
    ends at the ceiling. ``harness_text`` lists every message the harness
    wrote into the turn, with its cause where one is known.
    """
    stops = {}
    written = {}
    for row in native:
        message = row.get("message")
        if (
            row.get("type") != "assistant"
            or not isinstance(message, dict)
            or not message.get("id")
        ):
            continue
        if message.get("stop_reason"):
            stops[message["id"]] = message["stop_reason"]
        spent = (message.get("usage") or {}).get("output_tokens")
        if isinstance(spent, int):
            written[message["id"]] = max(spent, written.get(message["id"], 0))
    items = []
    for row in stream:
        # A response of a sub-agent belongs to that agent's thread, not to
        # the seat's reply.
        if row.get("parent_tool_use_id"):
            continue
        message = row.get("message")
        message = message if isinstance(message, dict) else {}
        if row.get("type") == "assistant":
            blocks = message.get("content")
            blocks = blocks if isinstance(blocks, list) else []
            ident = message.get("id")
            tools = any(
                isinstance(b, dict) and b.get("type") in ("tool_use", "server_tool_use")
                for b in blocks
            )
            # The stream writes one row per content block, so the rows of
            # one response share its id.
            last = items[-1] if items else {}
            if ident and last.get("kind") == "response" and last.get("id") == ident:
                last["text"] += text_of(blocks)
                last["tools"] = last["tools"] or tools
            else:
                items.append(
                    dict(kind="response", id=ident, text=text_of(blocks), tools=tools)
                )
        elif row.get("type") == "user":
            if row.get("isSynthetic"):
                items.append(dict(kind="harness", text=text_of(message.get("content"))))
            else:
                items.append(dict(kind="user"))
    result = [r for r in stream if r.get("type") == "result"][-1]
    responses = [i for i, item in enumerate(items) if item["kind"] == "response"]
    pieces = [result.get("result", "")]
    joined = set()
    k = responses[-1] if responses else None
    while (
        k is not None
        and k >= 2
        and items[k - 1]["kind"] == "harness"
        and items[k - 2]["kind"] == "response"
        and not items[k - 2]["tools"]
        and stops.get(items[k - 2]["id"]) == LIMIT
    ):
        pieces.insert(0, items[k - 2]["text"])
        joined.add(k - 1)
        k -= 2
    answer = "".join(pieces)
    offsets = {}
    at = 0
    for index, piece in zip(sorted(joined), pieces):
        at += len(piece)
        offsets[index] = at
    harness_text = []
    for i, item in enumerate(items):
        if item["kind"] != "harness":
            continue
        before = items[i - 1] if i else {}
        cause = (
            "output limit"
            if before.get("kind") == "response" and stops.get(before["id"]) == LIMIT
            else None
        )
        harness_text.append(
            dict(text=item["text"], cause=cause, answer_offset=offsets.get(i))
        )
    known = [items[i]["id"] for i in responses if items[i]["id"] in stops]
    output_limit = None
    if known:
        last = stops.get(items[responses[-1]]["id"])
        output_limit = dict(
            responses_at_limit=sum(stops[i] == LIMIT for i in known),
            # Where a part written before a cut ends and its resumption
            # begins. A response cut while it was still thinking wrote no
            # text, so it is a cut but not a join.
            answer_joined_at=sorted({o for o in offsets.values() if o}),
            answer_ends_at_limit=LIMIT in (last, result.get("stop_reason")),
            # The tokens each cut response had written, thinking included.
            # A cut falls at the ceiling the request carried, so this is
            # the only evidence of that ceiling the records hold.
            cut_at=sorted(
                {written[i] for i in known if stops[i] == LIMIT and i in written}
            ),
        )
    return answer, dict(output_limit=output_limit, harness_text=harness_text)


def flags(evidence, declared=None):
    """The red flags ``evidence`` from :func:`claude` raises, if any.

    ``declared`` is the output ceiling the seat was launched with
    (``output_ceiling``). A response cut at any other count shows the
    declaration did not hold; the answer stands, since the cut reply is
    still joined whole.
    """
    found = []
    limit = evidence.get("output_limit") or {}
    cuts = limit.get("cut_at") or []
    if declared is not None and any(cut != declared for cut in cuts):
        found.append(
            f"Output ceiling not as declared: declared {declared}, a response "
            f"was cut at {', '.join(str(c) for c in cuts)} tokens."
        )
    if limit.get("answer_ends_at_limit"):
        found.append(
            "The reply ends at the model's output limit, so the answer is cut "
            "off where the limit fell."
        )
    elif limit.get("responses_at_limit"):
        joins = limit.get("answer_joined_at") or []
        where = (
            "; the whole reply joins its parts verbatim at character "
            + ", ".join(f"{o:,}" for o in joins)
            if joins
            else ""
        )
        found.append(
            f"The reply reached the model's output limit "
            f"{_times(limit['responses_at_limit'])} and the harness asked the "
            f"model to resume{where}. The harness's own message is recorded "
            "as harness text and is no part of the reply."
        )
    if any(h["cause"] is None for h in evidence.get("harness_text", [])):
        found.append(
            "The harness wrote into this turn for a reason that cannot be "
            "classified. Its message is recorded as harness text. The answer "
            "is the seat's final response, so anything the seat wrote before "
            "the harness spoke is in the native session, not in the answer."
        )
    return found


def _times(count):
    return "once" if count == 1 else f"{count} times"

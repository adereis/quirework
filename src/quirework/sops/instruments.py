"""Versioned questions; no harness launch or filesystem exposure policy."""

from quirework.project import current_project
from quirework.sops import personas
from quirework.runs.storage import identifier

UNSET = object()


SCOPES = ("book", "artifact", "passage", "changes")


def load(name, *, root=None, _active=()):
    if root is None:
        root = current_project().root
    if not isinstance(name, str):
        raise ValueError("instrument name must be text")
    identifier(name)
    if name in _active:
        raise ValueError("cycle in instrument scope variants")
    path = current_project().path_at(root, "sops.instruments") / f"{name}.json"
    data = personas.parse(path.read_bytes().decode("utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("id") != name
        or not isinstance(data.get("revision"), str)
        or not data["revision"]
    ):
        raise ValueError(f"invalid instrument: {path}")
    prompts = data.get("prompts")
    if not isinstance(prompts, dict) or not prompts:
        raise ValueError("instrument requires prompts")
    default_scope = data.get("default_scope", "artifact")
    if default_scope not in SCOPES:
        raise ValueError("invalid instrument default scope")
    if "scope_notice" in data and type(data["scope_notice"]) is not bool:
        raise ValueError("scope_notice must be boolean")
    for ask, entry in prompts.items():
        if (
            not isinstance(ask, str)
            or not isinstance(entry, dict)
            or not isinstance(entry.get("text"), str)
            or not entry["text"].strip()
        ):
            raise ValueError("instrument asks require names and text")
        scopes = entry.get("scopes", [default_scope])
        if (
            not isinstance(scopes, list)
            or not scopes
            or any(s not in SCOPES for s in scopes)
        ):
            raise ValueError("invalid instrument ask scopes")
        headings = entry.get("headings", [])
        if not isinstance(headings, list) or any(
            not isinstance(h, str) for h in headings
        ):
            raise ValueError("instrument headings must be text")
        for scope in scopes:
            entry_text(data, ask, scope)
    sequence = data.get("sequence")
    if sequence is not None and (
        not isinstance(sequence, dict)
        or not {"first", "next"} <= set(sequence)
        or set(sequence) - {"first", "next", "wrap"}
    ):
        raise ValueError("sequence fields: first, next, optional wrap")
    for ask in [
        *(sequence or {}).values(),
        *([data["followup_ask"]] if "followup_ask" in data else []),
    ]:
        if not isinstance(ask, str) or ask not in prompts:
            raise ValueError("sequence or followup references an unknown ask")
    variants = data.get("scope_variants", {})
    if not isinstance(variants, dict):
        raise ValueError("scope_variants must map scopes to instruments and asks")
    for scope, variant in variants.items():
        if (
            scope not in SCOPES
            or not isinstance(variant, dict)
            or set(variant) != {"instrument", "ask"}
        ):
            raise ValueError("scope variant fields: instrument, ask")
        if any(not isinstance(v, str) for v in variant.values()):
            raise ValueError("scope variant targets must be names")
        target = load(variant["instrument"], root=root, _active=(*_active, name))
        entry_text(target, variant["ask"], scope)
    scope_asks = data.get("scope_asks", {})
    if not isinstance(scope_asks, dict):
        raise ValueError("scope_asks must map scopes to ask names")
    for scope, ask in scope_asks.items():
        if scope not in SCOPES or not isinstance(ask, str):
            raise ValueError("invalid scope ask")
        entry_text(data, ask, scope)
    if data.get("default_persona") is not None:
        reference = data["default_persona"]
        data["default_persona_reference"] = reference
        data["default_persona"] = {"frozen": personas.resolve(reference, root=root)}
    return data


def entry_text(instrument, ask, scope, active=()):
    if ask in active:
        raise ValueError("cycle in instrument includes")
    if ask not in instrument["prompts"]:
        raise ValueError(f"unknown instrument ask: {ask}")
    entry = instrument["prompts"][ask]
    if entry.get("scopes") and scope not in entry["scopes"]:
        raise ValueError(f"ask {ask} requires scope {entry['scopes']}, got {scope}")
    text, headings = entry["text"], entry.get("headings", [])
    includes = entry.get("includes", [])
    if not isinstance(includes, list) or any(not isinstance(a, str) for a in includes):
        raise ValueError("instrument includes must be ask names")
    for child in includes:
        extra, more = entry_text(instrument, child, scope, (*active, ask))
        text += "\n\n" + extra
        headings = [*headings, *more]
    return text, headings


def prompt(turn, instrument, *, persona=UNSET, first=True, scope=None):
    scope = scope or (instrument or {}).get("default_scope", "artifact")
    if "prompt" in turn:
        if not isinstance(turn["prompt"], str) or not turn["prompt"].strip():
            raise ValueError("turn prompt must be nonempty text")
        text, headings = turn["prompt"], turn.get("headings", [])
    else:
        if (
            instrument is None
            or not isinstance(turn.get("ask"), str)
            or turn["ask"] not in instrument["prompts"]
        ):
            raise ValueError("provide a prompt or a known instrument ask")
        text, headings = entry_text(instrument, turn["ask"], scope)
    addenda = turn.get("addenda", [])
    if (
        not isinstance(addenda, list)
        or any(not isinstance(a, str) for a in addenda)
        or len(set(addenda)) != len(addenda)
    ):
        raise ValueError("addenda must be a list of instrument ask names")
    for ask in addenda:
        if instrument is None or ask not in instrument["prompts"]:
            raise ValueError(f"unknown addendum: {ask}")
        extra, more = entry_text(instrument, ask, scope)
        text += "\n\n" + extra
        headings = [*headings, *more]
    if instrument and instrument.get("scope_notice") and first:
        text = f"Declared scope: {scope}.\n" + text
    if persona is UNSET:
        persona = (
            personas.resolve(instrument["default_persona"])
            if (
                instrument
                and instrument.get("default_persona")
                and "prompt" not in turn
            )
            else None
        )
    if persona is not None and (first or "{{persona}}" in text):
        text = personas.compose(text, persona)
    if "{{persona}}" in text:
        raise ValueError("persona slot has no resolved profile")
    return text, headings

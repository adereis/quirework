"""The most a Claude seat may write in one response, thinking included.

Claude Code sets this ceiling per model, and for some models its default is
half of what the model allows. A response that reaches it is cut, and the
harness asks the model to resume with a message that is not neutral: it
asks the model to break its remaining work into smaller pieces, which can
change the shape of everything after the cut. A project raises the ceiling
with ``providers.claude.output_ceiling``, and every driver freezes the
value into the seat it prepares, so a record keeps the ceiling it was
prepared with and an older record keeps the one it had.

The launch asks for the ceiling through ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``,
which the CLI applies to a streamed request up to the model's own ceiling.
Its usage table goes on reporting the per-model default whatever the launch
asked for, so a receipt cannot read the ceiling there. A cut response is
the evidence instead, since it stops at the ceiling the request carried;
:func:`quirework.providers.replies.flags` holds the cuts to the declaration.

Stdlib-only, like ``claude_login``: the run engine's frozen executor, the
rooms, the review driver and the experiment runner all read it.
"""

from __future__ import annotations

SETTING = "providers.claude.output_ceiling"
ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"


def validate(value, where=SETTING):
    """``value`` if it is a positive integer or None, and refuse anything else."""
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{where} must be a positive integer")
    return value


def apply(env, value):
    """Set the launch's ceiling in ``env``, or clear one ``env`` inherited.

    A launch whose seat declares no ceiling runs at the CLI's default, so a
    value left in the operator's own environment never reaches the seat.
    """
    if value is None:
        env.pop(ENV, None)
    else:
        env[ENV] = str(value)
    return env

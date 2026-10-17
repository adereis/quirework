"""The host's Claude login, as a jailed Claude seat borrows it.

A Claude seat runs in a blanked home and never sees the host's credentials
file, because a child holding the refresh token could rotate the login out
from under every other Claude process on the machine. It is handed only the
current OAuth access token, in ``CLAUDE_CODE_OAUTH_TOKEN``, which has no
refresh token behind it.

That token has a lifetime, and the host does not wait for the seat. Claude
Code on the host refreshes its login a few minutes before the access token
expires, and a refresh revokes the token it replaces. A seat still holding
the old copy then stops mid-turn on a 401: observed when a seat was launched
with a few minutes of its token left and another Claude process refreshed
the login two minutes in. Two defenses follow, and this module serves both.

The launch check refuses a token inside ``LAUNCH_MARGIN``, so a seat never
starts on a login the host is about to refresh. A run longer than the margin
can still meet a refresh, so the caller also records which token an attempt
was given, as a ``fingerprint`` (never the token itself), and after a 401
asks whether the host now holds a different one. When it does, the stopped
turn can be continued in its own session with the new login; when it does
not, the login really is gone and only the operator can sign in again.

Deliberately stdlib-only and free of project imports: the run engine's
frozen executor, the workshop, the review driver and the experiment runner
all read it.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

# Long enough that a launch is never minutes away from the host's own refresh,
# which Claude Code makes within the last few minutes of a token's life (the
# observed revocation came two minutes into a seat launched with under ten
# minutes left). Three times that window clears it with room. A longer margin
# would refuse launches for longer before each refresh and buy nothing more: a
# run outlasting any margin meets the refresh anyway, and the runner recovers
# that stop by continuing the turn with the new login.
LAUNCH_MARGIN = 15 * 60


class LoginError(RuntimeError):
    """The host has no usable Claude login for a seat to borrow."""


def credentials_path():
    """Where Claude Code keeps the host's login, under the current home."""
    return Path.home() / ".claude" / ".credentials.json"


def fingerprint(token):
    """A stable, non-reversible name for a token, safe to store in a record."""
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else None


def host_login(margin=LAUNCH_MARGIN, now=None):
    """``(token, fingerprint)`` for the host's current Claude access token.

    Raises ``LoginError`` when there is no token, or when it expires within
    ``margin`` seconds, with a message that says how long it has and what to
    do, because the operator is the only one who can act on it.
    """
    path = credentials_path()
    try:
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        oauth = {}
    token = oauth.get("accessToken")
    if not token:
        raise LoginError(
            f"no Claude login on this machine ({path} holds no access token): "
            "sign in with `claude` on the host, then retry"
        )
    now = time.time() if now is None else now
    left = (oauth.get("expiresAt") or 0) / 1000 - now
    if left < margin:
        state = (
            "has expired" if left <= 0 else f"expires in {max(1, round(left / 60))} min"
        )
        raise LoginError(
            f"the host's Claude login {state}, and a seat needs at least "
            f"{margin // 60} min of it: Claude Code renews the login shortly "
            "before it expires, and the renewal revokes the copy a seat holds, "
            "stopping it mid-turn. Retry once the host has renewed it (any "
            "use of `claude` on the host near expiry does), or sign in again "
            "with `claude`"
        )
    return token, fingerprint(token)

"""A Claude seat's borrowed login, and what happens when the host renews it.

A Claude seat is handed a copy of the host's access token. The host renews
its login near the token's expiry, which revokes that copy, so a long seat
can stop on a 401 halfway through its work. These tests pin the launch check
that keeps a seat off a token about to be renewed, the classifier that names
the stop, and the runner's recovery: continue the turn once the host holds a
newer token, and say plainly what to do when it does not.
"""

import json
import time

import pytest
from conftest import native_session

from quirework.providers import claude_login
from quirework.providers import harness
from quirework.providers import quota
from quirework.runs import engine

STOPPED = (
    "Failed to authenticate: OAuth token revoked. Please log in again or "
    "contact your administrator."
)


def plant_login(home, token, minutes):
    path = home / ".claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            dict(
                claudeAiOauth=dict(
                    accessToken=token,
                    refreshToken="fictional-refresh",
                    expiresAt=(time.time() + minutes * 60) * 1000,
                )
            )
        )
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def login_stop_rows(*, ran=True, structured=True):
    """A turn cut by a revoked token, in the shape Claude Code streams it."""
    rows = [dict(type="system", subtype="init", session_id="sid")]
    if ran:
        rows.append(
            dict(
                type="assistant",
                session_id="sid",
                message=dict(
                    model="fixture-model",
                    content=[dict(type="text", text="Half a lighthouse log.")],
                ),
            )
        )
    if structured:
        rows += [
            dict(
                type="system",
                subtype="api_retry",
                attempt=n,
                error="authentication_failed",
                error_status=401,
                session_id="sid",
            )
            for n in (1, 2)
        ]
    rows.append(
        dict(
            type="assistant",
            session_id="sid",
            error="authentication_failed" if structured else None,
            message=dict(
                model="fixture-model", content=[dict(type="text", text=STOPPED)]
            ),
        )
    )
    result = dict(
        type="result",
        subtype="success",
        is_error=True,
        session_id="sid",
        result=STOPPED,
        modelUsage={"fixture-model": dict(outputTokens=900 if ran else 0)},
    )
    if structured:
        result.update(api_error_status=401, terminal_reason="api_error")
    rows.append(result)
    return rows


def write_rows(record, rows):
    record.mkdir(parents=True, exist_ok=True)
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


# --------------------------------------------------------------------------- #
# the launch check
# --------------------------------------------------------------------------- #
def test_a_token_with_room_is_lent_and_fingerprinted(home):
    plant_login(home, "fictional-access", minutes=120)
    token, mark = claude_login.host_login()
    assert token == "fictional-access"
    assert mark == claude_login.fingerprint("fictional-access")
    assert "fictional-access" not in mark, "a fingerprint never holds the token"


def test_a_token_near_its_renewal_is_refused_with_what_to_do(home):
    plant_login(home, "fictional-access", minutes=8)
    with pytest.raises(claude_login.LoginError) as refused:
        claude_login.host_login()
    said = str(refused.value)
    assert "expires in 8 min" in said
    assert f"at least {claude_login.LAUNCH_MARGIN // 60} min" in said
    assert "sign in again with `claude`" in said


def test_an_expired_or_missing_login_is_refused(home):
    plant_login(home, "fictional-access", minutes=-1)
    with pytest.raises(claude_login.LoginError, match="has expired"):
        claude_login.host_login()
    (home / ".claude" / ".credentials.json").unlink()
    with pytest.raises(claude_login.LoginError, match="no Claude login"):
        claude_login.host_login()


def test_the_shared_runner_lends_only_the_access_token(home):
    plant_login(home, "fictional-access", minutes=120)
    env = harness.environment("claude")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fictional-access"
    assert "fictional-refresh" not in json.dumps(env)
    assert harness.token_fingerprint(env) == claude_login.fingerprint(
        "fictional-access"
    )


# --------------------------------------------------------------------------- #
# the classifier
# --------------------------------------------------------------------------- #
def test_a_revoked_login_is_its_own_stop_not_a_quota(tmp_path):
    record = tmp_path / "stopped"
    write_rows(record, login_stop_rows())
    assert quota.classify("claude", record) is None, "a login is not a quota limit"
    detail = harness.auth_stop("claude", record)
    assert detail["scope"] == "auth" and detail["phase"] == "interrupted"
    assert detail["session_id"] == "sid"


def test_a_login_that_failed_before_the_model_ran_is_refused(tmp_path):
    record = tmp_path / "refused"
    write_rows(record, login_stop_rows(ran=False))
    assert quota.auth_stop("claude", record)["phase"] == "refused"


def test_the_wording_alone_still_names_the_stop(tmp_path):
    record = tmp_path / "older"
    write_rows(record, login_stop_rows(structured=False))
    assert quota.auth_stop("claude", record)["scope"] == "auth"


def test_an_ordinary_fault_and_other_harnesses_are_not_login_stops(tmp_path):
    record = tmp_path / "fault"
    write_rows(
        record,
        [
            dict(
                type="result",
                subtype="success",
                is_error=True,
                session_id="sid",
                result="transport closed",
            )
        ],
    )
    assert quota.auth_stop("claude", record) is None
    write_rows(tmp_path / "codex", login_stop_rows())
    assert quota.auth_stop("codex", tmp_path / "codex") is None


def test_the_resumed_agent_is_told_the_login_was_renewed():
    text = quota.continuation(cause="auth")
    assert "login was renewed" in text and "do not start over" in text


# --------------------------------------------------------------------------- #
# the runner's recovery
# --------------------------------------------------------------------------- #
@pytest.fixture
def seat(tmp_path):
    (tmp_path / "one.md").write_text("The keeper wound the clock at dusk.\n")
    plan = dict(
        title="Fictitious scene",
        seats=[
            dict(
                id="reader",
                harness="claude",
                model="fixture-model",
                effort="high",
                turns=[
                    dict(
                        id="one",
                        prompt="Read this.",
                        materials=[dict(source="one.md", delivery="text")],
                    )
                ],
            )
        ],
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    return engine.Run(tmp_path / "record")


def stopped_on_login(run, *, ran=True, given="old-token"):
    write_rows(run.record("reader", "one"), login_stop_rows(ran=ran))
    native_session(run.workspace("reader"), "claude", "sid")
    state = run.state("reader")
    state["pending"] = dict(
        turn="one",
        mode="start",
        phase="receipt-pending",
        resume_point=[],
        outputs_before={},
        auth_fingerprint=claude_login.fingerprint(given),
    )
    run.save("reader", state)


def test_a_renewed_login_continues_the_turn_in_its_session(seat, home):
    stopped_on_login(seat)
    plant_login(home, "renewed-token", minutes=480)
    with pytest.raises(engine.AuthStopped):
        seat.attempt_turn("reader", 0, 1)
    seat.recover_auth(
        "reader", "one", quota.auth_stop("claude", seat.record("reader", "one"))
    )
    after = seat.state("reader")
    assert after["pending"] is None, "the stopped attempt is retired"
    assert after["session_id"] == "sid", "the same session is continued"
    assert after["continuation"]["scope"] == engine.AUTH_CONTINUATION
    assert after["auth_continuations"] == {"one": 1}
    assert "renewed login" in after["retired_attempts"]["one"]["0"]["reason"]


def test_turn_recovers_a_renewed_login_without_waiting(seat, home, monkeypatch):
    stopped_on_login(seat)
    plant_login(home, "renewed-token", minutes=480)
    calls = []

    def attempt(seat_name, index, timeout):
        calls.append(len(calls))
        if len(calls) == 1:
            raise engine.AuthStopped(
                quota.auth_stop("claude", seat.record("reader", "one"))
            )
        return "banked"

    monkeypatch.setattr(seat, "attempt_turn", attempt)
    assert seat.turn("reader", 0, 1, wait_quota=None) == "banked"
    assert calls == [0, 1], "the turn goes on at once, with no quota wait"


def test_an_unrenewed_login_stops_and_says_to_sign_in(seat, home):
    stopped_on_login(seat, given="same-token")
    plant_login(home, "same-token", minutes=480)
    detail = quota.auth_stop("claude", seat.record("reader", "one"))
    with pytest.raises(RuntimeError, match="sign in again with `claude`") as stop:
        seat.recover_auth("reader", "one", detail)
    assert "quirework sop runs continue" in str(stop.value)
    assert seat.state("reader")["pending"]["turn"] == "one", "the turn stays pending"


def test_a_login_too_close_to_renewal_is_not_used_to_continue(seat, home):
    stopped_on_login(seat)
    plant_login(home, "renewed-token", minutes=3)
    detail = quota.auth_stop("claude", seat.record("reader", "one"))
    with pytest.raises(RuntimeError, match="expires in 3 min"):
        seat.recover_auth("reader", "one", detail)
    assert seat.state("reader")["pending"] is not None


def test_a_login_that_keeps_failing_is_not_retried_forever(seat, home):
    stopped_on_login(seat)
    plant_login(home, "renewed-token", minutes=480)
    state = seat.state("reader")
    state["auth_continuations"] = {"one": engine.AUTH_CONTINUATION_CAP}
    seat.save("reader", state)
    detail = quota.auth_stop("claude", seat.record("reader", "one"))
    with pytest.raises(RuntimeError, match="failed again after 2 automatic"):
        seat.recover_auth("reader", "one", detail)
    assert seat.state("reader")["pending"] is not None


def test_a_login_refused_before_the_model_ran_resends_the_turn(seat, home):
    stopped_on_login(seat, ran=False)
    plant_login(home, "renewed-token", minutes=480)
    detail = quota.auth_stop("claude", seat.record("reader", "one"))
    seat.recover_auth("reader", "one", detail)
    after = seat.state("reader")
    assert after["pending"] is None and "continuation" not in after
    assert "resubmitted" in after["retired_attempts"]["one"]["0"]["reason"]

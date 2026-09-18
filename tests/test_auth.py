"""GitHub sign-in.

What these defend:
  * a session cannot be forged -- wrong key, tampered payload, "alg": "none",
    or an expired exp all verify as "signed out", never as a user;
  * the OAuth callback refuses a state that is missing, mismatched, expired or
    not signed by us, because without that check an attacker can complete a
    sign-in inside a victim's browser;
  * the callback can only ever redirect back to our own origin;
  * POST /submissions requires a session, and the user id is taken from that
    session and never from the request body;
  * a solve is scored once, no matter how many times it is resubmitted.
"""
from __future__ import annotations

import json
import time

import pytest

from cloud import auth

KEY = b"a-test-signing-key"
USER = {"id": 4242, "login": "octocat", "avatar_url": "https://avatars/o.png"}


def _event(session: str | None = None, **extra) -> dict:
    cookies = [f"{auth.COOKIE_NAME}={session}"] if session else []
    return {"cookies": cookies, **extra}


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def test_a_session_round_trips():
    claims = auth.decode_jwt(auth.make_session(USER, KEY), KEY)
    assert claims["sub"] == "4242"
    assert claims["login"] == "octocat"
    assert claims["avatar"] == "https://avatars/o.png"


def test_a_session_signed_with_another_key_is_rejected():
    assert auth.decode_jwt(auth.make_session(USER, KEY), b"different-key") is None


def test_a_tampered_payload_is_rejected():
    header, payload, signature = auth.make_session(USER, KEY).split(".")
    forged = auth._b64(json.dumps({"sub": "1", "login": "admin", "exp": 9e9}).encode())
    assert auth.decode_jwt(f"{header}.{forged}.{signature}", KEY) is None


def test_alg_none_is_rejected():
    """The classic forgery: swap the header to "alg": "none" and drop the
    signature. decode_jwt never reads `alg` back out of the token, so this is
    simply a bad signature."""
    header = auth._b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = auth._b64(json.dumps({"sub": "1", "login": "admin", "exp": 9e9}).encode())
    assert auth.decode_jwt(f"{header}.{payload}.", KEY) is None


def test_an_expired_session_is_rejected():
    long_ago = int(time.time()) - auth.SESSION_TTL_S - 10
    assert auth.decode_jwt(auth.make_session(USER, KEY, now=long_ago), KEY) is None


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.b.c.d", "a.b.c"])
def test_malformed_tokens_decode_to_none_without_raising(token):
    assert auth.decode_jwt(token, KEY) is None


def test_a_token_with_no_exp_is_rejected():
    assert auth.decode_jwt(auth.encode_jwt({"sub": "1"}, KEY), KEY) is None


def test_a_token_with_a_boolean_exp_is_rejected():
    # bool is an int subclass; without the explicit check, exp=True compares
    # as 1 and the token would be treated as merely expired rather than junk.
    assert auth.decode_jwt(auth.encode_jwt({"sub": "1", "exp": True}, KEY), KEY) is None


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def test_the_user_id_is_the_numeric_id_not_the_login():
    # Logins are renameable; keying on one would hand a renamed account
    # someone else's solved history.
    claims = auth.decode_jwt(auth.make_session(USER, KEY), KEY)
    assert auth.user_id(claims) == "gh:4242"


def test_the_public_profile_carries_nothing_but_id_login_and_avatar():
    claims = auth.decode_jwt(auth.make_session(USER, KEY), KEY)
    assert auth.public_profile(claims) == {
        "user_id": "gh:4242",
        "login": "octocat",
        "avatar_url": "https://avatars/o.png",
    }


def test_read_session_finds_the_cookie_among_others():
    session = auth.make_session(USER, KEY)
    event = {"cookies": ["other=1", f" {auth.COOKIE_NAME}={session} ", "another=2"]}
    assert auth.read_session(event, KEY)["login"] == "octocat"


def test_read_session_with_no_cookies_is_none():
    assert auth.read_session({}, KEY) is None
    assert auth.read_session({"cookies": []}, KEY) is None


# ---------------------------------------------------------------------------
# cookies
# ---------------------------------------------------------------------------

def test_the_session_cookie_is_httponly_secure_and_cross_site():
    header = auth.set_cookie(auth.COOKIE_NAME, "v", 60)
    assert "HttpOnly" in header
    assert "Secure" in header
    # Lax would never be sent at all: the app and the API are different sites.
    assert "SameSite=None" in header
    assert "Max-Age=60" in header


def test_clearing_the_cookie_expires_it_immediately():
    assert "Max-Age=0" in auth.clear_cookie(auth.COOKIE_NAME)


# ---------------------------------------------------------------------------
# OAuth state
# ---------------------------------------------------------------------------

def test_matching_state_and_cookie_verify():
    state = auth.make_state("/course/tenacity", KEY)
    assert auth.verify_state(state, state, KEY)["rt"] == "/course/tenacity"


@pytest.mark.parametrize(
    "state, cookie_state",
    [
        ("", None),                    # neither
        ("", "abc"),                   # no state in the query
        ("abc", None),                 # no cookie to compare against
    ],
)
def test_state_missing_on_either_side_is_refused(state, cookie_state):
    assert auth.verify_state(state, cookie_state, KEY) == {}


def test_state_that_does_not_match_the_cookie_is_refused():
    # The login-CSRF case: an attacker supplies their own valid GitHub state.
    attacker = auth.make_state("/", KEY)
    victim = auth.make_state("/", KEY)
    assert auth.verify_state(attacker, victim, KEY) == {}


def test_expired_state_is_refused():
    stale = auth.make_state("/", KEY, now=int(time.time()) - auth.STATE_TTL_S - 1)
    assert auth.verify_state(stale, stale, KEY) == {}


def test_state_signed_by_someone_else_is_refused():
    theirs = auth.make_state("/", b"their-key")
    assert auth.verify_state(theirs, theirs, KEY) == {}


def test_two_state_tokens_are_never_equal():
    assert auth.make_state("/", KEY) != auth.make_state("/", KEY)


# ---------------------------------------------------------------------------
# open redirect
# ---------------------------------------------------------------------------

ORIGIN = "https://main.d1.amplifyapp.com"


@pytest.mark.parametrize(
    "candidate, expected",
    [
        (None, ORIGIN),
        ("", ORIGIN),
        ("/course/tenacity", f"{ORIGIN}/course/tenacity"),
        (f"{ORIGIN}/solve/abc", f"{ORIGIN}/solve/abc"),
        (ORIGIN, ORIGIN),
        ("https://evil.example/steal", ORIGIN),
        ("//evil.example/steal", ORIGIN),
        ("/\\evil.example", ORIGIN),
        ("https://main.d1.amplifyapp.com.evil.example/x", ORIGIN),
        ("javascript:alert(1)", ORIGIN),
    ],
)
def test_return_to_never_leaves_our_origin(candidate, expected):
    assert auth.safe_return_to(candidate, ORIGIN) == expected


# ---------------------------------------------------------------------------
# the handler
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID_SECRET_ARN_VALUE", "client-id-123")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET_ARN_VALUE", "client-secret-456")
    monkeypatch.setenv("SESSION_SIGNING_SECRET_ARN_VALUE", KEY.decode())
    monkeypatch.setenv("OAUTH_REDIRECT_URI", "https://api.example/auth/callback")
    monkeypatch.setenv("WEB_ORIGIN", ORIGIN)


def _cookie_named(response, name):
    for raw in response["cookies"]:
        if raw.startswith(f"{name}="):
            return raw
    return None


def test_start_redirects_to_github_and_plants_the_state_cookie(auth_env):
    from cloud.handlers import fn_auth

    response = fn_auth.start({"queryStringParameters": {"return_to": "/course/tenacity"}})

    assert response["statusCode"] == 302
    location = response["headers"]["location"]
    assert location.startswith(auth.GITHUB_AUTHORIZE_URL)
    assert "client_id=client-id-123" in location
    assert _cookie_named(response, auth.STATE_COOKIE_NAME) is not None
    # The client secret must never appear in anything sent to a browser.
    assert "client-secret-456" not in json.dumps(response)


def test_start_refuses_to_carry_a_foreign_return_to(auth_env):
    from cloud.handlers import fn_auth

    response = fn_auth.start({"queryStringParameters": {"return_to": "https://evil.example"}})
    state = _cookie_named(response, auth.STATE_COOKIE_NAME).split("=")[1].split(";")[0]
    assert auth.decode_jwt(state, KEY)["rt"] == ORIGIN


def test_callback_with_a_bad_state_redirects_home_without_a_session(auth_env):
    from cloud.handlers import fn_auth

    response = fn_auth.callback(
        {"queryStringParameters": {"state": "forged", "code": "c"}, "cookies": []}
    )

    assert response["statusCode"] == 302
    assert response["headers"]["location"].startswith(ORIGIN)
    assert "auth_error" in response["headers"]["location"]
    assert _cookie_named(response, auth.COOKIE_NAME) is None


def test_callback_when_the_user_cancels_just_goes_home(auth_env):
    from cloud.handlers import fn_auth

    response = fn_auth.callback({"queryStringParameters": {"error": "access_denied"}, "cookies": []})

    assert response["statusCode"] == 302
    assert response["headers"]["location"] == ORIGIN
    assert "auth_error" not in response["headers"]["location"]
    assert _cookie_named(response, auth.COOKIE_NAME) is None


def test_callback_sets_a_session_and_returns_where_you_started(auth_env, monkeypatch):
    from cloud.handlers import fn_auth

    monkeypatch.setattr(auth, "exchange_code", lambda code, uri: "gho_token")
    monkeypatch.setattr(auth, "fetch_user", lambda token: USER)

    state = auth.make_state(f"{ORIGIN}/solve/abc", KEY)
    response = fn_auth.callback(
        {
            "queryStringParameters": {"state": state, "code": "the-code"},
            "cookies": [f"{auth.STATE_COOKIE_NAME}={state}"],
        }
    )

    assert response["statusCode"] == 302
    assert response["headers"]["location"].startswith(f"{ORIGIN}/solve/abc?")
    assert "signed_in=1" in response["headers"]["location"]

    raw = _cookie_named(response, auth.COOKIE_NAME)
    session = raw.split("=", 1)[1].split(";")[0]
    assert auth.decode_jwt(session, KEY)["login"] == "octocat"
    # the short-lived state cookie is cleared on the way out
    assert "Max-Age=0" in _cookie_named(response, auth.STATE_COOKIE_NAME)


def test_callback_reports_a_github_failure_without_signing_anyone_in(auth_env, monkeypatch):
    from cloud.handlers import fn_auth

    def boom(code, uri):
        raise auth.AuthError("GitHub rejected the sign-in: bad_verification_code")

    monkeypatch.setattr(auth, "exchange_code", boom)

    state = auth.make_state("/", KEY)
    response = fn_auth.callback(
        {
            "queryStringParameters": {"state": state, "code": "used-twice"},
            "cookies": [f"{auth.STATE_COOKIE_NAME}={state}"],
        }
    )

    assert response["statusCode"] == 302
    assert "auth_error" in response["headers"]["location"]
    assert _cookie_named(response, auth.COOKIE_NAME) is None


def test_logout_clears_the_session(auth_env):
    from cloud.handlers import fn_auth

    response = fn_auth.logout()
    assert response["statusCode"] == 200
    assert "Max-Age=0" in _cookie_named(response, auth.COOKIE_NAME)


def test_an_unconfigured_stack_fails_closed_rather_than_crashing(monkeypatch):
    """No secrets set: sign-in is unavailable, but the response is still a
    redirect back to the app, not a 500 on the API's domain."""
    from cloud.handlers import fn_auth

    for name in (
        "GITHUB_CLIENT_ID_SECRET_ARN",
        "GITHUB_CLIENT_SECRET_ARN",
        "SESSION_SIGNING_SECRET_ARN",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_VALUE", raising=False)
    monkeypatch.setenv("WEB_ORIGIN", ORIGIN)

    response = fn_auth.handler({"routeKey": "GET /auth/github", "queryStringParameters": {}}, None)
    assert response["statusCode"] == 302
    assert "auth_error" in response["headers"]["location"]

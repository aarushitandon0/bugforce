"""
The sign-in endpoints. The only function whose role can read the GitHub
client secret.

    GET  /auth/github    -> 302 to github.com, state cookie set
    GET  /auth/callback  <- 302 back to the web app, session cookie set
    POST /auth/logout    -> clears the session cookie

The callback never renders anything. On both success and failure it redirects
to the web app, with `?signed_in=1` or `?auth_error=<message>` -- a learner who
denied the GitHub prompt should land back where they started, not on a JSON
error page on the API's domain.

fn_api handles GET /auth/me itself: reading a session only needs the signing
key, and routing it through here would give the profile endpoint the right to
talk to GitHub as the application.
"""
from __future__ import annotations

import logging
import urllib.parse

from cloud import auth, config

log = logging.getLogger()
log.setLevel(logging.INFO)


def redirect_uri() -> str:
    """The callback URL GitHub sends the code to.

    Registered on the GitHub OAuth app and sent on both legs of the flow;
    GitHub compares them, so this must be one literal string, not derived
    from the incoming request (which an attacker controls via Host).
    """
    return config.env("OAUTH_REDIRECT_URI")


def web_origin() -> str:
    return config.env("WEB_ORIGIN", "")


def _redirect(location: str, cookies: list[str]) -> dict:
    return {
        "statusCode": 302,
        "headers": {"location": location, "cache-control": "no-store"},
        "cookies": cookies,
        "body": "",
    }


def _back_to_app(return_to: str, **params) -> str:
    target = auth.safe_return_to(return_to, web_origin())
    if not params:
        return target
    joiner = "&" if "?" in target else "?"
    return f"{target}{joiner}{urllib.parse.urlencode(params)}"


def start(event: dict) -> dict:
    query = event.get("queryStringParameters") or {}
    return_to = auth.safe_return_to(query.get("return_to"), web_origin())

    key = auth.signing_key()
    state = auth.make_state(return_to, key)
    return _redirect(
        auth.authorize_url(state, redirect_uri()),
        [auth.set_cookie(auth.STATE_COOKIE_NAME, state, auth.STATE_TTL_S)],
    )


def callback(event: dict) -> dict:
    query = event.get("queryStringParameters") or {}
    key = auth.signing_key()
    cookie_state = auth.cookie(event, auth.STATE_COOKIE_NAME)
    drop_state = auth.clear_cookie(auth.STATE_COOKIE_NAME)

    # The user pressed Cancel on GitHub's prompt. Not an error worth shouting
    # about -- put them back where they were.
    if query.get("error"):
        return _redirect(_back_to_app(None), [drop_state])

    claims = auth.verify_state(query.get("state", ""), cookie_state, key)
    if not claims:
        # Mismatched, missing, expired or forged state. Deliberately vague:
        # the distinction is only useful to whoever is probing it.
        log.warning("rejecting callback with bad state")
        return _redirect(
            _back_to_app(None, auth_error="sign-in expired or was tampered with; try again"),
            [drop_state],
        )

    return_to = auth.safe_return_to(claims.get("rt"), web_origin())
    code = query.get("code")
    if not code:
        return _redirect(_back_to_app(return_to, auth_error="GitHub sent no code"), [drop_state])

    try:
        user = auth.fetch_user(auth.exchange_code(code, redirect_uri()))
    except auth.AuthError as e:
        log.warning("sign-in failed: %s", e)
        return _redirect(_back_to_app(return_to, auth_error=str(e)), [drop_state])

    session = auth.make_session(user, key)
    log.info("signed in %s", user["login"])
    return _redirect(
        _back_to_app(return_to, signed_in="1"),
        [drop_state, auth.set_cookie(auth.COOKIE_NAME, session, auth.SESSION_TTL_S)],
    )


def logout() -> dict:
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "cookies": [auth.clear_cookie(auth.COOKIE_NAME)],
        "body": '{"signed_out": true}',
    }


def handler(event: dict, context) -> dict:
    route = event.get("routeKey", "")
    try:
        if route == "GET /auth/github":
            return start(event)
        if route == "GET /auth/callback":
            return callback(event)
        if route == "POST /auth/logout":
            return logout()
    except auth.AuthError as e:
        log.warning("auth misconfigured on %s: %s", route, e)
        return _redirect(_back_to_app(None, auth_error="sign-in is not configured"), [])
    except Exception:
        log.exception("unhandled error on %s", route)
        return _redirect(_back_to_app(None, auth_error="sign-in failed"), [])

    return {
        "statusCode": 404,
        "headers": {"content-type": "application/json"},
        "body": '{"error": "no such auth route"}',
    }

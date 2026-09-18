"""
GitHub sign-in: state tokens, session cookies, and the GitHub calls.

Everything in this module is pure except `exchange_code` and `fetch_user`,
which are the only two functions that talk to github.com. That split is
deliberate -- the signing, verification and cookie handling are where the
security actually lives, and they are unit-testable without a network.

**Sessions are signed, not stored.** A session is an HS256 JWT carrying the
GitHub numeric id, login and avatar, and nothing else. There is no session
table to look up and nothing to revoke; a session simply expires. That is the
right trade here because a session grants exactly one thing -- the right to
submit a patch as yourself -- and nothing a stolen one could do is worth a
round trip to DynamoDB on every request.

**Two separate secrets, on purpose.** The GitHub client secret is readable
only by fn_auth's role; it is the credential that can impersonate the whole
application to GitHub. The session signing key is readable by fn_auth (which
signs) and fn_api (which verifies), and can do nothing beyond mint sessions
for this stack. Collapsing them into one secret would hand every route the
ability to talk to GitHub as the app.

**The cookie is cross-site.** The web app is on Amplify and the API is on
execute-api, so the session cookie must be `SameSite=None; Secure` and the API
must send `Access-Control-Allow-Credentials: true` against a *specific*
origin. `WebOrigin="*"` and credentials are mutually exclusive by spec, so
deploying auth requires a real WebOrigin -- see FEATURES.md, "Deploying".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

COOKIE_NAME = "bf_session"
STATE_COOKIE_NAME = "bf_oauth_state"

SESSION_TTL_S = 7 * 24 * 3600
STATE_TTL_S = 600
HTTP_TIMEOUT_S = 10

# Only the login and avatar are ever requested. BugForge reads nothing from a
# user's account, so the empty scope (public profile) is all it asks for.
GITHUB_SCOPE = ""

_secrets_client = None
_secret_cache: dict[str, str] = {}


class AuthError(RuntimeError):
    """Raised when a sign-in cannot be completed. The message is user-facing."""


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def _secrets():
    global _secrets_client
    if _secrets_client is None:
        import boto3

        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def _secret(env_name: str) -> str:
    """Reads a secret by ARN from the environment, cached for the container's life.

    A local/test override (`<ENV_NAME>_VALUE`) exists so the whole auth path can
    be exercised without AWS; it is never set in the deployed template.
    """
    inline = os.environ.get(f"{env_name}_VALUE")
    if inline:
        return inline
    arn = os.environ.get(env_name)
    if not arn:
        raise AuthError(f"{env_name} is not configured")
    if arn not in _secret_cache:
        _secret_cache[arn] = _secrets().get_secret_value(SecretId=arn)["SecretString"]
    return _secret_cache[arn]


def client_id() -> str:
    return _secret("GITHUB_CLIENT_ID_SECRET_ARN")


def client_secret() -> str:
    return _secret("GITHUB_CLIENT_SECRET_ARN")


def signing_key() -> bytes:
    return _secret("SESSION_SIGNING_SECRET_ARN").encode("utf-8")


# ---------------------------------------------------------------------------
# JWT (HS256, hand-rolled)
# ---------------------------------------------------------------------------
# Hand-rolled rather than PyJWT because the container image is built once per
# repo on a trusted machine and every dependency added there is one more thing
# to vet. HS256 signing is twelve lines; the parts that matter are that
# verification is constant-time and that `alg` is never read back out of the
# token itself (the "alg: none" family of forgeries).

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(message: bytes, key: bytes) -> str:
    return _b64(hmac.new(key, message, hashlib.sha256).digest())


def encode_jwt(claims: dict, key: bytes) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    message = f"{header}.{payload}".encode("ascii")
    return f"{header}.{payload}.{_sign(message, key)}"


def decode_jwt(token: str, key: bytes, now: int | None = None) -> dict | None:
    """Returns the claims, or None for anything that is not a valid, unexpired
    token signed by `key`. Never raises -- every caller treats a bad token and
    no token identically."""
    if not token or token.count(".") != 2:
        return None
    header_b64, payload_b64, signature = token.split(".")
    # The expected algorithm is fixed here; `alg` in the header is never
    # consulted, so "alg": "none" and HS256/RS256 confusion cannot apply.
    expected = _sign(f"{header_b64}.{payload_b64}".encode("ascii"), key)
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        claims = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    if exp <= (time.time() if now is None else now):
        return None
    return claims


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def make_session(user: dict, key: bytes, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    return encode_jwt(
        {
            "sub": str(user["id"]),
            "login": user["login"],
            "avatar": user.get("avatar_url", ""),
            "iat": now,
            "exp": now + SESSION_TTL_S,
        },
        key,
    )


def read_session(event: dict, key: bytes) -> dict | None:
    """The signed-in user for this request, or None. The single source of
    identity -- no handler may take a user id from a request body."""
    return decode_jwt(cookie(event, COOKIE_NAME) or "", key)


def user_id(claims: dict) -> str:
    """The leaderboard/progress key: "gh:12345".

    The numeric GitHub id, never the login, because logins are renameable and
    a renamed login would otherwise be handed someone else's solved history.
    The prefix keeps the pre-existing "anonymous" rows unambiguous.
    """
    return f"gh:{claims['sub']}"


def public_profile(claims: dict) -> dict:
    return {
        "user_id": user_id(claims),
        "login": claims.get("login", ""),
        "avatar_url": claims.get("avatar", ""),
    }


# ---------------------------------------------------------------------------
# cookies
# ---------------------------------------------------------------------------

def cookie(event: dict, name: str) -> str | None:
    for raw in event.get("cookies") or []:
        key, _, value = raw.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def set_cookie(name: str, value: str, max_age: int) -> str:
    # SameSite=None is required, not lax security: the web app and the API are
    # on different registrable domains, so a Lax cookie would never be sent on
    # the app's fetch() at all. Secure is mandatory alongside it.
    return f"{name}={value}; Path=/; HttpOnly; Secure; SameSite=None; Max-Age={max_age}"


def clear_cookie(name: str) -> str:
    return f"{name}=; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=0"


# ---------------------------------------------------------------------------
# the OAuth state token
# ---------------------------------------------------------------------------
# `state` is signed and echoed in a short-lived cookie, and both must match on
# the way back. Without it, an attacker can complete a sign-in in a victim's
# browser using their own GitHub code, silently attaching the victim's
# submissions to the attacker's account.

def make_state(return_to: str, key: bytes, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    return encode_jwt(
        {
            "nonce": secrets.token_urlsafe(16),
            "rt": return_to,
            "iat": now,
            "exp": now + STATE_TTL_S,
        },
        key,
    )


def verify_state(state: str, cookie_state: str | None, key: bytes) -> dict:
    """Both copies must be present, identical, and validly signed."""
    if not state or not cookie_state:
        return {}
    if not hmac.compare_digest(state, cookie_state):
        return {}
    return decode_jwt(state, key) or {}


def safe_return_to(candidate: str | None, web_origin: str) -> str:
    """Only ever redirect back to our own origin.

    An open redirect on a callback is how an OAuth flow turns into a phishing
    primitive, so anything that is not a same-origin absolute URL or a plain
    relative path is replaced by the app root.
    """
    root = web_origin.rstrip("/")
    if not candidate:
        return root or "/"
    if candidate.startswith("//") or "\\" in candidate:
        return root or "/"
    if candidate.startswith("/"):
        return f"{root}{candidate}"
    if root and (candidate == root or candidate.startswith(f"{root}/")):
        return candidate
    return root or "/"


# ---------------------------------------------------------------------------
# github
# ---------------------------------------------------------------------------

def authorize_url(state: str, redirect_uri: str) -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "state": state,
        "allow_signup": "true",
    }
    if GITHUB_SCOPE:
        params["scope"] = GITHUB_SCOPE
    return f"{GITHUB_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_json(url: str, data: dict, headers: dict) -> dict:
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, headers: dict) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def exchange_code(code: str, redirect_uri: str) -> str:
    """Trades the one-time code for an access token. Returns the token."""
    try:
        payload = _post_json(
            GITHUB_TOKEN_URL,
            {
                "client_id": client_id(),
                "client_secret": client_secret(),
                "code": code,
                "redirect_uri": redirect_uri,
            },
            {"Accept": "application/json", "User-Agent": "bugforge"},
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise AuthError("could not reach GitHub to complete sign-in") from e

    # GitHub answers 200 with {"error": ...} for a reused or expired code.
    if payload.get("error"):
        detail = payload.get("error_description") or payload["error"]
        raise AuthError(f"GitHub rejected the sign-in: {detail}")
    token = payload.get("access_token")
    if not token:
        raise AuthError("GitHub returned no access token")
    return token


def fetch_user(access_token: str) -> dict:
    """Reads the profile. Only id, login and avatar_url are ever kept."""
    try:
        user = _get_json(
            GITHUB_USER_URL,
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "bugforge",
            },
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise AuthError("could not read your GitHub profile") from e

    if not user.get("id") or not user.get("login"):
        raise AuthError("GitHub profile is missing an id or login")
    return {"id": user["id"], "login": user["login"], "avatar_url": user.get("avatar_url", "")}

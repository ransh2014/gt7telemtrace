"""
auth.py -- Supabase anonymous auth for TRACE's onboarding flow (Phase 5).

The `laps` table's INSERT RLS policy now requires `auth.uid() = user_id`,
so submitting a lap needs a real authenticated session, not just the
public anon key. Supabase's anonymous auth provider gets a player from
"no account" to "signed in enough to submit a lap" with nothing more than
a display name -- no email, no password, ever.

Like leaderboard.py and analytics.py, this module is stdlib-only (urllib),
never raises to the caller, and every function has a short network timeout
so the onboarding screen (or the submit button) never hangs the UI waiting
on a bad connection.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from .config import explain_error

__all__ = ["sign_up_anonymous", "refresh_session", "set_display_name", "last_refresh_error", "last_signup_error"]

_SUPABASE_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"

# Why the last refresh_session() call failed: "invalid" (the server rejected
# the refresh token -- the account was logged out elsewhere or removed as
# unused), "network" (couldn't reach it / server error), or None (success).
# Callers use it to tell "sign in again" apart from "try again later"
# without refresh_session() having to raise.
last_refresh_error = None

# Why the last sign_up_anonymous() call failed: "certs" (TLS certificate check failed --
# usually missing system CA certificates), "dns", "timeout", "http:<code>", "network`,
# or None (success). Lets the UI say what went wrong instead of a generic message.
last_signup_error = None


def _parse_session(body: dict) -> dict:
    user = body.get("user") or {}
    return {
        "access_token": body.get("access_token", ""),
        "refresh_token": body.get("refresh_token", ""),
        "user_id": user.get("id", ""),
    }


def sign_up_anonymous(timeout: float = 8):
    """Create a new anonymous Supabase auth session (no email or password)
    -- a real auth.users row (and, via the profiles trigger, a `profiles`
    row) that satisfies RLS's `auth.uid() = user_id` check on lap
    submission. Returns {"access_token", "refresh_token", "user_id"} on
    success, None on any failure (offline, Supabase down, anonymous auth
    disabled, malformed response). Never raises."""
    global last_signup_error
    last_signup_error = None
    try:
        data = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/auth/v1/signup",
            data=data, method="POST",
            headers={
                "apikey": _SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {_SUPABASE_ANON_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            session = _parse_session(json.loads(resp.read().decode("utf-8")))
            if session["access_token"] and session["user_id"]:
                return session
            last_signup_error = "network"
            return None
    except Exception as e:
        last_signup_error = explain_error(e)
        return None


def _token_rejected(err: "urllib.error.HTTPError") -> bool:
    """True only when the server says the refresh token itself is bad (400/401
    with an invalid_grant / refresh-token style body). Rate limits (429),
    timeouts (408), 5xx and malformed-request errors are NOT a verdict on the
    token -- treating them as one used to delete working accounts."""
    if err.code not in (400, 401):
        return False
    try:
        body = err.read().decode("utf-8", "replace").lower()
    except Exception:
        return False
    return any(s in body for s in ("invalid_grant", "refresh_token_not_found", "refresh_token_already_used",
                                   "invalid refresh token", "refresh token not found",
                                   "already used", "session_not_found"))


def refresh_session(refresh_token: str, timeout: float = 8):
    """Exchange a stored refresh_token for a fresh access_token once the
    old one expires (Supabase access tokens are short-lived, ~1h). Returns
    the same {"access_token", "refresh_token", "user_id"} shape as
    sign_up_anonymous() on success, None on any failure (including an
    empty/missing refresh_token). Never raises; sets last_refresh_error."""
    global last_refresh_error
    if not refresh_token:
        last_refresh_error = "invalid"
        return None
    try:
        data = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            data=data, method="POST",
            headers={
                "apikey": _SUPABASE_ANON_KEY,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            session = _parse_session(json.loads(resp.read().decode("utf-8")))
            if not session["access_token"]:
                last_refresh_error = "network"
                return None
            if not session["refresh_token"]:
                # Never replace a good stored refresh token with an empty one.
                session["refresh_token"] = refresh_token
            last_refresh_error = None
            return session
    except urllib.error.HTTPError as e:
        last_refresh_error = "invalid" if _token_rejected(e) else "network"
        return None
    except Exception:
        last_refresh_error = "network"
        return None


def set_display_name(access_token: str, user_id: str, display_name: str, timeout: float = 8) -> bool:
    """Set display_name on the caller's own `profiles` row. Uses
    access_token -- not the anon key -- as the Authorization bearer, since
    the profiles UPDATE policy checks `auth.uid() = id`. Returns True on
    success, False on any failure (including missing access_token/user_id).
    Never raises."""
    if not access_token or not user_id:
        return False
    try:
        data = json.dumps({"display_name": display_name}).encode("utf-8")
        user_id_q = urllib.parse.quote(str(user_id), safe="")
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id_q}",
            data=data, method="PATCH",
            headers={
                "apikey": _SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                # return=representation, not return=minimal: a PATCH that
                # matches zero rows (missing profile, or RLS filtering it out)
                # still comes back 204, so "no exception" was never proof the
                # name actually landed. Asking for the row back means we can
                # tell a real write from a silent no-op -- which is the whole
                # point of the honest "name didn't sync" message added in 0.2.2.
                "Prefer": "return=representation",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        rows = json.loads(body) if body.strip() else []
        return bool(rows) and rows[0].get("display_name") == display_name
    except Exception:
        return False

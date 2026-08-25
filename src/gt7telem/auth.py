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

__all__ = ["sign_up_anonymous", "refresh_session", "set_display_name"]

_SUPABASE_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"


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
            return session if session["access_token"] and session["user_id"] else None
    except Exception:
        return None


def refresh_session(refresh_token: str, timeout: float = 8):
    """Exchange a stored refresh_token for a fresh access_token once the
    old one expires (Supabase access tokens are short-lived, ~1h). Returns
    the same {"access_token", "refresh_token", "user_id"} shape as
    sign_up_anonymous() on success, None on any failure (including an
    empty/missing refresh_token). Never raises."""
    if not refresh_token:
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
            return session if session["access_token"] else None
    except Exception:
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
                "Prefer": "return=minimal",
            },
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False

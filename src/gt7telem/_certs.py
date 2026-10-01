"""
_certs.py -- make HTTPS work when the bundled Python's built-in CA path doesn't exist.

A PyInstaller binary built on Ubuntu looks for CA certs at /usr/lib/ssl/cert.pem
(or /usr/lib/ssl/certs). Other distros keep them elsewhere -- Fedora/RHEL under
/etc/pki, openSUSE under /var/lib/ca-certificates, Arch under /etc/ca-certificates,
NixOS under /etc/ssl/certs -- and some (newer Fedora) ship only a directory of
hashed certs with no single bundle file. Without a fix every HTTPS call fails
certificate verification.

ensure_ca_bundle() runs once when this module is first imported (gt7telem/__init__.py
imports it before anything touches the network). It does nothing on Windows (OS store),
nothing if the user set SSL_CERT_FILE / SSL_CERT_DIR, and nothing if Python's default CA
file or dir already exists. Otherwise it tries, in order: a known system bundle file that
actually loads, a known system cert directory, then certifi's bundled copy. Verification
stays ON. It never raises.

explain_error(exc) turns a network exception into a short category so the UI can say
*why* a request failed instead of a generic "couldn't reach the server".
"""

import os
import socket
import ssl
import sys

__all__ = ["ensure_ca_bundle", "explain_error", "CA_CANDIDATES", "CA_DIR_CANDIDATES"]

CA_CANDIDATES = (
    "/etc/pki/tls/certs/ca-bundle.crt",                    # Fedora / RHEL / CentOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",   # Fedora / RHEL (extracted)
    "/etc/ssl/certs/ca-certificates.crt",                  # Debian / Ubuntu / Arch / Gentoo
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",    # Arch (extracted)
    "/var/lib/ca-certificates/ca-bundle.pem",              # openSUSE (extracted)
    "/etc/ssl/ca-bundle.pem",                              # openSUSE
    "/etc/ssl/certs/ca-bundle.crt",                        # NixOS / some others
    "/etc/pki/tls/cacert.pem",                             # older RHEL
    "/usr/share/ssl/certs/ca-bundle.crt",                  # very old RHEL
    "/etc/ssl/cert.pem",                                   # Alpine / macOS / BSD
    "/usr/local/share/certs/ca-root-nss.crt",              # FreeBSD
    "/usr/local/etc/ssl/cert.pem",                         # FreeBSD / OpenBSD ports
    "/data/data/com.termux/files/usr/etc/tls/cert.pem",    # Termux
)

# Hashed cert directories, used only when no bundle file works (e.g. newer Fedora).
CA_DIR_CANDIDATES = (
    "/etc/pki/tls/certs",
    "/etc/ssl/certs",
    "/etc/openssl/certs",
    "/var/lib/ca-certificates/openssl",
    "/etc/ca-certificates/extracted/cadir",
)


def _usable_file(path: str) -> bool:
    """True if `path` is a CA bundle OpenSSL can actually load (not empty/garbage)."""
    try:
        if not os.path.isfile(path):
            return False
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=path)
        return True
    except Exception:
        return False


def _usable_dir(path: str) -> bool:
    """True if `path` looks like a hashed cert dir (has c_rehash-style *.0 entries)."""
    try:
        return os.path.isdir(path) and any(n.endswith(".0") for n in os.listdir(path))
    except Exception:
        return False


def ensure_ca_bundle() -> str | None:
    """Point OpenSSL at a CA store if Python's default has none. Returns what was set, else None."""
    try:
        if sys.platform.startswith("win"):
            return None
        if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
            return None
        paths = ssl.get_default_verify_paths()
        if (paths.cafile and os.path.isfile(paths.cafile)) or (
            paths.capath and os.path.isdir(paths.capath)
        ):
            return None
        for cand in CA_CANDIDATES:
            if _usable_file(cand):
                os.environ["SSL_CERT_FILE"] = cand
                return cand
        for d in CA_DIR_CANDIDATES:
            if _usable_dir(d):
                os.environ["SSL_CERT_DIR"] = d
                return d
        try:
            import certifi

            cand = certifi.where()
            if _usable_file(cand):
                os.environ["SSL_CERT_FILE"] = cand
                return cand
        except Exception:
            pass
    except Exception:
        pass
    return None


def explain_error(exc: BaseException) -> str:
    """Classify a network exception: "certs", "dns", "timeout", "http:<code>" or "network"."""
    try:
        import urllib.error

        if isinstance(exc, urllib.error.HTTPError):
            return f"http:{exc.code}"
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
            return "certs"
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return "certs"
        if isinstance(reason, socket.gaierror):
            return "dns"
        if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout)):
            return "timeout"
    except Exception:
        pass
    return "network"


ensure_ca_bundle()

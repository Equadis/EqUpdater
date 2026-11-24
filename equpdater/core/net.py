"""Secure networking: hardened TLS, HTTPS-only redirects, host allowlist.

Lifted unchanged from ``equpdater/app.py`` so the Qt front end and the Tk one
share it (and so it can be imported without Tk). ``app.py`` re-exports every
name, so existing call sites and tests are untouched.
"""

import os
import ssl
import sys
import urllib.request
from urllib.parse import urlsplit

from .. import platforms

# ──────────────────────────────────────────────────────────────────────────────
#  Secure networking
# ──────────────────────────────────────────────────────────────────────────────

#: System CA bundles on Linux distributions, by family. Used besides certifi:
#: a frozen build's own OpenSSL looks for the build machine's paths, which
#: another distribution need not have.
SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                  # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",                    # Fedora, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",   # Fedora, newer
    "/etc/ssl/ca-bundle.pem",                              # openSUSE
    "/etc/ssl/cert.pem",                                   # Alpine, others
)


def ca_bundles() -> list:
    """The CA bundles EqUpdater trusts, besides the system's own store.

    certifi's curated roots always: on Windows, Python's ssl reads a static
    snapshot of the root store and never triggers Windows' on-demand root
    update, and under Wine that store is empty. In a frozen build certifi's
    file is looked for directly in the bundle as well, so a packaging quirk
    in certifi's own lookup cannot quietly leave the app with no roots.
    On Linux the distribution's bundle is added too."""
    found = []
    try:
        import certifi
        found.append(certifi.where())
    except Exception:
        pass
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        found.append(os.path.join(bundle, "certifi", "cacert.pem"))
    if not platforms.WINDOWS:
        found.extend(SYSTEM_CA_BUNDLES)
    out = []
    for path in found:
        if path and os.path.isfile(path) and path not in out:
            out.append(path)
    return out


def make_ssl_context():
    """(context, CA files loaded). Hardened TLS: the server certificate is
    verified, the hostname must match, and nothing below TLS 1.2 is
    accepted -- the primary defence against a man-in-the-middle tampering
    with downloads. Never relaxed for any platform."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    loaded = []
    for path in ca_bundles():
        try:
            ctx.load_verify_locations(path)
            loaded.append(path)
        except (OSError, ssl.SSLError):
            continue
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass
    return ctx, loaded


SSL_CTX, CA_SOURCES = make_ssl_context()

# Binaries may only be fetched from these hosts. TLS already stops a MITM from
# impersonating them; this additionally stops a tampered API response from
# redirecting a download (e.g. a mod DLL) to an unexpected host.
ALLOWED_DOWNLOAD_HOSTS = {
    "octowow.st",
    "dl.octowow.st",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeberg.org",
}


def _check_url(url: str, allowed_hosts):
    """Enforce HTTPS and (optionally) an allowlist on a URL."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS URL: {url}")
    if allowed_hosts is not None:
        host = (parts.hostname or "").lower()
        if host not in allowed_hosts:
            raise RuntimeError(f"Refusing download from unexpected host: {host}")


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Require every redirect target to stay HTTPS (blocks an https→http
    downgrade). The host allowlist is deliberately *not* re-applied on
    redirects: an allowlisted host controls its own redirects – legitimately
    to its CDN (e.g. octowow.st→dl.octowow.st, github.com→codeload) – and TLS
    protects wherever it lands. The allowlist's job is to vet the *initial*
    URL (against a tampered API response), which secure_urlopen still does."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl, None)   # HTTPS-only, no host check
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Shared opener with the hardened TLS context and the HTTPS-only redirect
# guard, built once.
_SECURE_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=SSL_CTX),
    _HttpsOnlyRedirectHandler())


def secure_urlopen(req, timeout, allowed_hosts=None):
    """urlopen wrapper that enforces HTTPS + an optional host allowlist on the
    initial URL, keeps redirects on HTTPS, and uses the hardened TLS context.
    `req` may be a URL string or a urllib Request."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    _check_url(url, allowed_hosts)
    return _SECURE_OPENER.open(req, timeout=timeout)

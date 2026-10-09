"""Conservative link policy with DNS pinning and redirect validation.

Requests follows redirects (allow_redirects=True), but our Session validates
EVERY prepared destination before the adapter can connect. A cross-host redirect
is blocked before contacting the new host; same-host redirects are followed.
Thus a chain that temporarily changes host cannot evade policy by returning to
the original host. A TLS adapter connects to a vetted public IP while preserving
TLS SNI/certificate hostname checks, closing the DNS-rebinding check/use gap.
This is a policy heuristic, not a commercial threat-intelligence feed.
"""

import ipaddress
import re
import socket
import time
from urllib.parse import urlsplit, urlunsplit
import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool

TRUSTED_DOMAINS = {
    "linkedin.com",
    "indeed.com",
    "naukri.com",
    "glassdoor.com",
    "foundit.in",
    "wellfound.com",
    "greenhouse.io",
    "lever.co",
    "workdayjobs.com",
}
SUSPICIOUS_TLDS = {"xyz", "top", "tk"}


class PolicyBlock(ValueError):
    def __init__(self, reason, domain=""):
        super().__init__(reason)
        self.domain = domain


class NetworkUnknown(ValueError):
    pass


def parse_url(url):
    if len(url) > 2048 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise PolicyBlock("Malformed URL blocked.")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise PolicyBlock(
                "Only HTTP(S) URLs without embedded credentials are allowed."
            )
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not host or len(host) > 253:
            raise PolicyBlock("Invalid domain.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in (80, 443):
            raise PolicyBlock("Nonstandard network port blocked.", host)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, PolicyBlock):
            raise
        raise PolicyBlock("Invalid URL.") from exc
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise PolicyBlock("Raw IP address used instead of a domain.", host)
    if host.split(".")[-1] in SUSPICIOUS_TLDS:
        raise PolicyBlock("Suspicious domain ending: ." + host.split(".")[-1], host)
    if (
        "." not in host
        or host.endswith((".localhost", ".local", ".internal", ".test"))
        or host == "localhost"
    ):
        raise PolicyBlock("Private or local destination blocked.", host)
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise PolicyBlock("Invalid domain.", host)
    netloc = (
        host if port == (443 if parsed.scheme == "https" else 80) else f"{host}:{port}"
    )
    normalized = urlunsplit(
        (parsed.scheme, netloc, parsed.path or "/", parsed.query, "")
    )
    return normalized, host, port


def public_addresses(host, port):
    try:
        addresses = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        )
    except socket.gaierror as exc:
        raise NetworkUnknown("Domain could not be resolved.") from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise PolicyBlock("Destination resolves to a non-public network address.", host)
    return addresses


def trusted(host):
    # Suffix boundary avoids accepting linkedin.com.attacker.example.
    return any(
        host == domain or host.endswith("." + domain) for domain in TRUSTED_DOMAINS
    )


class PinnedAdapter(HTTPAdapter):
    """One adapter per request chain; no global DNS mutation or disabled TLS."""

    def __init__(self, host, address, port, scheme):
        super().__init__(max_retries=0)
        self.host = host
        self.address = address
        self.port = port
        self.scheme = scheme
        self.pool = None

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        # requests >=2.32 uses this method. The host remains in the URL and Host
        # header, but the underlying socket connects only to our validated IP.
        parsed = urlsplit(request.url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.hostname != self.host:
            raise PolicyBlock(
                "Unexpected destination change blocked.", parsed.hostname or ""
            )
        if self.pool is not None:
            self.pool.close()
        if parsed.scheme == "https":
            self.pool = HTTPSConnectionPool(
                self.address, port, assert_hostname=self.host, server_hostname=self.host
            )
        else:
            self.pool = HTTPConnectionPool(self.address, port)
        return self.pool

    def add_headers(self, request, **kwargs):
        parsed = urlsplit(request.url)
        request.headers["Host"] = parsed.netloc
        request.headers["Accept-Encoding"] = "identity"

    def close(self):
        if self.pool is not None:
            self.pool.close()
        super().close()


class GuardedSession(requests.Session):
    def __init__(self, origin, addresses, port, scheme):
        super().__init__()
        self.origin = origin
        self.deadline = time.monotonic() + 12
        self.max_redirects = 5
        self.trust_env = False
        self.mount("http://", PinnedAdapter(origin, addresses[0], port, scheme))
        self.mount("https://", PinnedAdapter(origin, addresses[0], port, scheme))
        self.visited = []
        self.hooks["response"].append(self._redirect_headers)

    def _redirect_headers(self, response, *args, **kwargs):
        # Requests normally consumes a redirect body before the next request.
        # Close it immediately: a hostile redirect body cannot stream forever.
        if response.is_redirect:
            response.close()
            response._content = b""
            response._content_consumed = True
        return response

    def send(self, request, **kwargs):
        _, host, _ = parse_url(request.url)
        if host != self.origin:
            raise PolicyBlock(
                f"Cross-domain redirect blocked: {self.origin} → {host}.", host
            )
        if (
            self.visited
            and urlsplit(self.visited[-1]).scheme == "https"
            and urlsplit(request.url).scheme == "http"
        ):
            raise PolicyBlock("HTTPS to HTTP downgrade blocked.", host)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise NetworkUnknown("Redirect inspection time budget exceeded.")
        kwargs["timeout"] = (min(3, remaining), min(4, remaining))
        kwargs["stream"] = True
        self.visited.append(request.url)
        return super().send(request, **kwargs)


def check_url(url):
    """hard_block is authoritative policy; network failures are UNKNOWN, not safe."""
    host = ""
    try:
        normalized, host, port = parse_url(url)
        addresses = public_addresses(host, port)
        with GuardedSession(
            host, addresses, port, urlsplit(normalized).scheme
        ) as session:
            with session.get(normalized, allow_redirects=True, stream=True) as response:
                _, final_host, _ = parse_url(response.url)
                if final_host != host:
                    raise PolicyBlock(
                        "Final destination domain differs from original.", final_host
                    )
                if response.status_code >= 400:
                    raise NetworkUnknown(
                        f"Link returned HTTP {response.status_code}; reputation could not be established."
                    )
                is_trusted = trusted(final_host)
                return {
                    "hard_block": False,
                    "unknown": False,
                    "risk": 0.1 if is_trusted else 0.5,
                    "domain": final_host,
                    "final_url": response.url,
                    "hops": len(response.history),
                    "reasons": [
                        (
                            "Known job platform domain; listing authenticity is not verified."
                            if is_trusted
                            else "Domain is not in the trusted-platform list; this alone is not proof of fraud."
                        ),
                        f"{len(response.history)} same-domain redirect(s) inspected.",
                    ],
                }
    except PolicyBlock as exc:
        return {
            "hard_block": True,
            "unknown": False,
            "risk": 1.0,
            "domain": exc.domain or host,
            "final_url": None,
            "reasons": [str(exc)],
        }
    except (requests.RequestException, NetworkUnknown, OSError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, NetworkUnknown)
            else "Link inspection failed or timed out; verify the destination manually."
        )
        return {
            "hard_block": False,
            "unknown": True,
            "risk": None,
            "domain": host,
            "final_url": None,
            "reasons": [reason],
        }

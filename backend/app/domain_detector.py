"""Domain status detector - multi-signal detection for expired/parked domains.

Detection signals (from most to least reliable):
  1. DNS NXDOMAIN          → domain doesn't exist (deleted/expired)
  2. NS record matching    → known registrar parking nameservers
  3. CNAME pointing to     → known parking/redirect services
  4. A record IP matching  → known parking IP ranges
  5. WHOIS expiration date → domain actually expired (optional, port 43 often blocked)
  6. HTTP response content → keyword matching in page body (least reliable, as fallback)

Each signal produces evidence with a confidence score. Final verdict is based on
weighted combination of all signals.
"""
import asyncio
import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import dns.resolver
import dns.asyncresolver
from loguru import logger


# ============================================================
# Signal 1: Known parking / registrar NS patterns
# ============================================================
# These are nameserver hostnames used by registrars for parked/expired domains.
# Compiled from real-world observations. Each entry is a substring match.
PARKING_NS_PATTERNS = [
    # Generic parking
    "parking", "parked", "sedoparking", "parkingcrew",
    "above.com", "bodis.com", "undeveloped.com",
    # Expiry-specific NS (e.g. Gname, GoDaddy)
    "expire", "expired",
    "gname-dns.com",        # Gname expired NS (jdz2che.com case)
    "domaincontrol.com",    # GoDaddy (sometimes parking)
    # Chinese registrars parking
    "parkdns", "parkinglot", "dnspod-free",
    "nodns.dnspod.net",     # DNSPod placeholder when expired
    "f1g1ns1.dnspod.net",   # Free tier / parked
    "parking.xinnet.com",   # 新网 parking
    "ns.hostmonster.com",   # HostMonster default
    "ns.bluehost.com",      # Bluehost default
    # Afternic / Dan.com
    "afternic", "dan.com",
    # NameSilo / Namesilo parking
    "dnsv.jp", "domaindefend",
    # Sav.com
    "sav.com",
    # HugeDomains
    "hugedomains",
    # Porkbun
    "porkbun.com/parking",
    # West263 / 西部数码
    "west263.com/parking", "west-dnsxx",
    # Dynadot
    "dynadot.com",
    # NameBright
    "namebright",
    # PUSHDO
    "push.dns",
]

# ============================================================
# Signal 2: Known parking CNAME targets
# ============================================================
PARKING_CNAME_PATTERNS = [
    "exp.gname.net",        # Gname expired redirect
    "parking.above.com",
    "sedoparking.com",
    "bodis.com",
    "parkingcrew.net",
    "undeveloped.com",
    "afternic.com",
    "dan.com",
    "hugedomains.com",
    "domainlore.com",
    "parked.com",
    "parkingpage.namecheap.com",
    "forward.gname.net",
    "park.domainc.com",
    "domainsbyproxy.com",
    "expired.hostmonster.com",
    "sedo.com",
    "dsredirection",
    "gname.net",            # Gname generic
]

# ============================================================
# Signal 3: Known parking IP ranges (CIDR blocks)
# ============================================================
# Some registrars/parking services use fixed IP ranges.
PARKING_IP_RANGES = [
    # Sedo
    "208.91.196.0/23", "208.91.198.0/24",
    # Bodis
    "199.59.242.0/24", "199.59.243.0/24",
    # Above.com
    "67.225.146.0/24",
    # ParkingCrew
    "208.73.208.0/21",
    # GoDaddy parked
    "184.168.131.0/24", "34.102.136.180/32",
    # Afternic
    "97.74.104.0/24",
    # 常见注册商停放页 IP (Chinese)
    "43.242.166.0/24",      # 西部数码停放
    "103.224.182.0/24",     # 部分国内停放
]

# Pre-parse into ipaddress networks for fast lookup
_PARKING_NETWORKS = []
for cidr in PARKING_IP_RANGES:
    try:
        _PARKING_NETWORKS.append(ipaddress.ip_network(cidr, strict=False))
    except ValueError:
        pass

# ============================================================
# Signal 4: HTTP response content keywords (kept as fallback)
# ============================================================
EXPIRED_PAGE_KEYWORDS = [
    # English - high confidence
    "domain has expired", "this domain has expired", "domain name has expired",
    "domain is expired", "this domain is for sale", "buy this domain",
    "this domain may be for sale", "domain parking", "this domain is parked",
    "parked free", "parked by", "sedoparking",
    "hugedomains", "afternic", "sav.com",
    "renew your domain", "domain renewal",
    "this site is no longer available",
    "this account has been suspended", "account suspended",
    "hosting has expired", "website expired",
    # English - medium confidence (more generic)
    "domain for sale", "expired domain",
    # Chinese - high confidence
    "域名过期", "域名已过期", "域名到期",
    "域名出售", "域名转让", "此域名可出售",
    "域名停放", "该域名已过期",
    "域名未续费", "域名续费",
    "域名已暂停", "网站已到期",
    "虚拟主机已到期", "主机已过期", "空间已到期",
    "请联系域名注册商",
    # Chinese - medium confidence
    "万网", "新网", "西部数码",
]


@dataclass
class DomainSignal:
    """A single detection signal."""
    signal_type: str        # dns_nxdomain, ns_parking, cname_parking, ip_parking, whois_expired, content_keyword
    confidence: float       # 0.0 ~ 1.0
    detail: str             # Human-readable detail


@dataclass
class DomainVerdict:
    """Final domain status verdict."""
    is_parked_or_expired: bool   # True only for NS/CNAME/IP parking signals
    is_dns_not_found: bool = False  # True only for NXDOMAIN (separate from parked)
    confidence: float = 0.0      # 0.0 ~ 1.0
    category: str = "normal"     # normal, domain_expired, domain_parked, domain_not_found, registrar_held
    summary: str = ""            # One-line Chinese summary for display
    signals: list[DomainSignal] = field(default_factory=list)


def _extract_domain(url: str) -> str:
    """Extract bare domain from URL."""
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Remove port if present
    host = host.split(":")[0]
    return host.lower()


def _sync_resolve_ns(domain: str, timeout: float = 5.0) -> list[str]:
    """Synchronous NS resolution. Handles CNAME + NS edge cases better than async."""
    sync_resolver = dns.resolver.Resolver()
    sync_resolver.lifetime = timeout
    sync_resolver.timeout = timeout
    try:
        answers = sync_resolver.resolve(domain, "NS")
        return [str(r.target).rstrip(".").lower() for r in answers]
    except dns.resolver.NoAnswer:
        # Try parent zone
        parts = domain.split(".")
        if len(parts) > 2:
            parent = ".".join(parts[-2:])
            answers = sync_resolver.resolve(parent, "NS")
            return [str(r.target).rstrip(".").lower() for r in answers]
    return []


async def _resolve_dns(domain: str, timeout: float = 5.0) -> dict:
    """Resolve DNS records for a domain. Returns dict with A, NS, CNAME info."""
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout

    result = {
        "a_records": [],
        "ns_records": [],
        "cname_records": [],
        "nxdomain": False,
        "no_answer": False,
        "error": None,
    }

    # --- A records ---
    try:
        answers = await resolver.resolve(domain, "A")
        result["a_records"] = [r.address for r in answers]
    except dns.resolver.NXDOMAIN:
        result["nxdomain"] = True
        return result  # No point checking NS/CNAME if domain doesn't exist
    except dns.resolver.NoAnswer:
        result["no_answer"] = True
    except dns.resolver.NoNameservers:
        result["error"] = "no_nameservers"
    except Exception as e:
        result["error"] = f"a_error: {str(e)[:100]}"

    # --- NS records ---
    # NS records are typically on the zone apex. The async resolver can sometimes
    # return NoAnswer when the domain has a CNAME. Fall back to sync resolver in
    # a thread executor for reliability.
    try:
        answers = await resolver.resolve(domain, "NS")
        result["ns_records"] = [str(r.target).rstrip(".").lower() for r in answers]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        # Async resolver failed — try sync resolver in thread (handles CNAME + NS better)
        try:
            loop = asyncio.get_event_loop()
            ns_list = await loop.run_in_executor(None, _sync_resolve_ns, domain, timeout)
            result["ns_records"] = ns_list
        except Exception:
            pass
    except Exception:
        pass

    # --- CNAME (query directly, may raise NoAnswer if A record exists) ---
    try:
        answers = await resolver.resolve(domain, "CNAME")
        result["cname_records"] = [str(r.target).rstrip(".").lower() for r in answers]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        pass
    except Exception:
        pass

    return result


def _check_ns_signals(ns_records: list[str]) -> Optional[DomainSignal]:
    """Check if NS records match known parking nameservers."""
    for ns in ns_records:
        ns_lower = ns.lower()
        for pattern in PARKING_NS_PATTERNS:
            if pattern in ns_lower:
                return DomainSignal(
                    signal_type="ns_parking",
                    confidence=0.90,
                    detail=f"NS '{ns}' 匹配停放模式 '{pattern}'",
                )
    return None


def _check_cname_signals(cname_records: list[str]) -> Optional[DomainSignal]:
    """Check if CNAME points to known parking services."""
    for cname in cname_records:
        cname_lower = cname.lower()
        for pattern in PARKING_CNAME_PATTERNS:
            if pattern in cname_lower:
                return DomainSignal(
                    signal_type="cname_parking",
                    confidence=0.95,
                    detail=f"CNAME '{cname}' 指向停放服务 '{pattern}'",
                )
    return None


def _check_ip_signals(a_records: list[str]) -> Optional[DomainSignal]:
    """Check if IP addresses fall in known parking ranges."""
    for ip_str in a_records:
        try:
            ip = ipaddress.ip_address(ip_str)
            for network in _PARKING_NETWORKS:
                if ip in network:
                    return DomainSignal(
                        signal_type="ip_parking",
                        confidence=0.80,
                        detail=f"IP {ip_str} 在已知停放段 {network}",
                    )
        except ValueError:
            pass
    return None


def check_content_signals(body_text: str) -> Optional[DomainSignal]:
    """Check HTTP response body for parking/expired keywords."""
    if not body_text:
        return None
    text_lower = body_text.lower()[:50000]
    for kw in EXPIRED_PAGE_KEYWORDS:
        if kw.lower() in text_lower:
            return DomainSignal(
                signal_type="content_keyword",
                confidence=0.60,
                detail=f"页面内容匹配: '{kw}'",
            )
    return None


async def detect_domain_status(
    url: str,
    response_body: Optional[str] = None,
    dns_timeout: float = 5.0,
) -> DomainVerdict:
    """
    Multi-signal domain status detection.

    Call flow:
      1. Extract domain from URL
      2. DNS resolve (A, NS, CNAME)
      3. Check each signal
      4. Combine signals into final verdict

    Returns DomainVerdict with category and confidence.
    """
    domain = _extract_domain(url)
    if not domain:
        return DomainVerdict(
            is_parked_or_expired=False,
            confidence=0.0,
            category="normal",
            summary="",
            signals=[],
        )

    signals: list[DomainSignal] = []

    # --- DNS resolution ---
    try:
        dns_info = await _resolve_dns(domain, timeout=dns_timeout)
    except Exception as e:
        logger.debug(f"DNS resolution failed for {domain}: {e}")
        dns_info = {"a_records": [], "ns_records": [], "cname_records": [],
                     "nxdomain": False, "no_answer": False, "error": str(e)}

    # Signal: NXDOMAIN
    if dns_info["nxdomain"]:
        signals.append(DomainSignal(
            signal_type="dns_nxdomain",
            confidence=0.99,
            detail=f"域名 {domain} DNS 不存在 (NXDOMAIN)",
        ))

    # Signal: NS parking
    if dns_info["ns_records"]:
        ns_signal = _check_ns_signals(dns_info["ns_records"])
        if ns_signal:
            signals.append(ns_signal)

    # Signal: CNAME parking
    if dns_info["cname_records"]:
        cname_signal = _check_cname_signals(dns_info["cname_records"])
        if cname_signal:
            signals.append(cname_signal)

    # Signal: IP parking
    if dns_info["a_records"]:
        ip_signal = _check_ip_signals(dns_info["a_records"])
        if ip_signal:
            signals.append(ip_signal)

    # Signal: Content keywords (lowest priority)
    if response_body:
        content_signal = check_content_signals(response_body)
        if content_signal:
            signals.append(content_signal)

    # --- Combine signals into verdict ---
    if not signals:
        return DomainVerdict(
            is_parked_or_expired=False,
            confidence=0.0,
            category="normal",
            summary="",
            signals=[],
        )

    # Highest confidence signal drives the decision
    max_confidence = max(s.confidence for s in signals)
    # Multiple signals boost confidence
    combined = min(1.0, max_confidence + 0.05 * (len(signals) - 1))

    # Determine category
    signal_types = {s.signal_type for s in signals}

    if "dns_nxdomain" in signal_types:
        # NXDOMAIN = domain simply doesn't resolve.
        # This is NOT the same as parked/expired. Separate category.
        return DomainVerdict(
            is_parked_or_expired=False,
            is_dns_not_found=True,
            confidence=combined,
            category="domain_not_found",
            summary=f"DNS无法解析 (NXDOMAIN)",
            signals=signals,
        )
    elif "ns_parking" in signal_types and ("cname_parking" in signal_types or "ip_parking" in signal_types):
        category = "domain_expired"
        summary = "域名过期/注册商停放"
    elif "ns_parking" in signal_types:
        category = "domain_expired"
        ns_detail = next(s.detail for s in signals if s.signal_type == "ns_parking")
        summary = f"域名过期/注册商处 ({ns_detail})"
    elif "cname_parking" in signal_types:
        category = "domain_parked"
        summary = "域名停放 (CNAME指向停放服务)"
    elif "ip_parking" in signal_types:
        category = "domain_parked"
        summary = "域名停放 (IP指向停放服务)"
    elif "content_keyword" in signal_types:
        # Content-only signal, lower confidence
        if combined >= 0.6:
            category = "registrar_held"
            kw_detail = next(s.detail for s in signals if s.signal_type == "content_keyword")
            summary = f"疑似域名过期/注册商处 ({kw_detail})"
        else:
            return DomainVerdict(
                is_parked_or_expired=False, confidence=combined,
                category="normal", summary="", signals=signals,
            )
    else:
        category = "domain_parked"
        summary = "疑似域名停放"

    is_expired = combined >= 0.50

    return DomainVerdict(
        is_parked_or_expired=is_expired,
        confidence=round(combined, 2),
        category=category,
        summary=summary,
        signals=signals,
    )


# ============================================================
# Synchronous wrapper for use in non-async contexts
# ============================================================
def detect_domain_status_sync(
    url: str,
    response_body: Optional[str] = None,
    dns_timeout: float = 5.0,
) -> DomainVerdict:
    """Sync wrapper around detect_domain_status."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context, create new loop in thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    detect_domain_status(url, response_body, dns_timeout),
                )
                return future.result(timeout=dns_timeout + 2)
        else:
            return loop.run_until_complete(
                detect_domain_status(url, response_body, dns_timeout)
            )
    except Exception as e:
        logger.debug(f"Sync domain detection failed: {e}")
        return DomainVerdict(
            is_parked_or_expired=False, confidence=0.0,
            category="normal", summary="", signals=[],
        )

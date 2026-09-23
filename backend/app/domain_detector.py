"""Domain status detector - pure DNS-based detection for expired/parked domains.

Detection signals:
  1. DNS NXDOMAIN          → domain doesn't exist (deleted/expired/unregistered)
  2. NS record matching    → authoritative nameserver is a known parking/expired DNS server
  3. CNAME pointing to     → CNAME record points to known parking/redirect services
  4. A record IP matching  → A record resolves to known parking IP ranges
  5. DNS server mismatch   → domain's DNS server does not match expected DNS server

NO string/keyword matching on HTML page content is performed.
All determinations are strictly based on the domain's authoritative DNS servers and records.
"""
import asyncio
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import dns.resolver
import dns.asyncresolver
from loguru import logger


# ============================================================
# Signal 1: Known parking / registrar expired NS patterns
# ============================================================
# Authoritative nameservers used by registrars when a domain is parked or expired.
PARKING_NS_PATTERNS = [
    # Generic parking
    "parking", "parked", "sedoparking", "parkingcrew",
    "above.com", "bodis.com", "undeveloped.com",
    "dan.com", "afternic",
    "hugedomains", "domainlore", "parked.com",
    # Expiry-specific NS
    "expire", "expired",
    # Gname expired / parking NS (e.g. jdz2che.com case)
    "gname-dns.com", "exp.gname.net",
    # DNSPod expired / held / parking
    "nodns.dnspod.net", "f1g1ns1.dnspod.net", "parkdns", "parkinglot", "dnspod-free",
    # 新网 parking
    "parking.xinnet.com", "xinnetdns.com/parking",
    # 阿里 / 万网 parking
    "parking.aliyun.com", "park.hichina.com", "dns.hichina.com/parking",
    # 西部数码 parking
    "parking.west.cn", "west263.com/parking", "west-dnsxx",
    # NameSilo parking
    "dnsv.jp", "domaindefend",
    # Sav.com parking
    "sav.com",
    # Porkbun parking
    "porkbun.com/parking", "parking.porkbun.com",
    # Dynadot parking
    "parking.dynadot.com",
    # NameBright parking
    "parking.namebright.com",
    # GoDaddy parking
    "parked.domaincontrol.com",
    # HostMonster / Bluehost parking
    "parking.hostmonster.com", "parking.bluehost.com",
    # PUSHDO
    "push.dns",
]

# ============================================================
# Signal 2: Known parking CNAME targets
# ============================================================
PARKING_CNAME_PATTERNS = [
    "exp.gname.net",
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
    "gname.net",
]

# ============================================================
# Signal 3: Known parking IP ranges (CIDR blocks)
# ============================================================
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
    # 西部数码停放
    "43.242.166.0/24",
    # 国内常见停放
    "103.224.182.0/24",
]

_PARKING_NETWORKS = []
for cidr in PARKING_IP_RANGES:
    try:
        _PARKING_NETWORKS.append(ipaddress.ip_network(cidr, strict=False))
    except ValueError:
        pass


COMMON_TWO_PART_TLDS = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "mil.cn", "ac.cn",
    "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk",
    "co.jp", "ne.jp", "or.jp", "ac.jp", "ad.jp", "ed.jp", "go.jp",
    "com.hk", "org.hk", "net.hk", "edu.hk", "gov.hk",
    "com.tw", "org.tw", "net.tw", "edu.tw", "gov.tw",
    "co.kr", "ne.kr", "or.kr", "re.kr",
    "com.au", "net.au", "org.au", "edu.au",
    "co.nz", "net.nz", "org.nz",
    "com.sg", "net.sg", "org.sg",
    "com.my", "net.my", "org.my",
}


def get_apex_domain(domain: str) -> str:
    """Extract root/apex domain from full domain or subdomain.
    Examples:
        www.example.com -> example.com
        sub.shop.example.com -> example.com
        www.example.com.cn -> example.com.cn
        example.com -> example.com
        192.168.1.1 -> 192.168.1.1
    """
    domain = domain.lower().strip(".")
    try:
        ipaddress.ip_address(domain)
        return domain
    except ValueError:
        pass

    parts = domain.split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    two_part = ".".join(parts[-2:])
    if two_part in COMMON_TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _extract_domain(url: str) -> str:
    """Extract hostname without port from URL."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return host.split(":")[0].lower()


@dataclass
class DomainSignal:
    signal_type: str        # dns_nxdomain, ns_parking, cname_parking, ip_parking, dns_mismatch, dns_error
    confidence: float       # 0.0 ~ 1.0
    detail: str             # Human-readable explanation


@dataclass
class DomainVerdict:
    is_parked_or_expired: bool
    is_dns_not_found: bool = False
    is_dns_error: bool = False
    confidence: float = 0.0
    category: str = "normal"  # normal, domain_expired, domain_parked, domain_not_found, dns_mismatch, dns_error
    summary: str = ""
    dns_server: Optional[str] = None  # Comma-separated nameservers (e.g. "ns1.alidns.com, ns2.alidns.com")
    ns_records: list[str] = field(default_factory=list)
    signals: list[DomainSignal] = field(default_factory=list)


# In-memory TTL cache for DNS query results (300 seconds TTL)
_DNS_CACHE: dict[str, tuple[float, dict]] = {}
_DNS_CACHE_TTL = 300.0


async def _resolve_dns(domain: str, timeout: float = 3.0) -> dict:
    """Resolve DNS records for domain with in-memory caching and apex NS fallback."""
    now = time.monotonic()
    if domain in _DNS_CACHE:
        ts, cached = _DNS_CACHE[domain]
        if now - ts < _DNS_CACHE_TTL:
            return cached

    apex = get_apex_domain(domain)
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout

    result = {
        "domain": domain,
        "apex": apex,
        "a_records": [],
        "ns_records": [],
        "cname_records": [],
        "nxdomain": False,
        "no_nameservers": False,
        "error": None,
    }

    # 1. Query NS records
    # Authoritative NS records live at the zone apex. If querying subdomain returns
    # NoAnswer, fall back to apex.
    targets_for_ns = [apex] if apex == domain else [apex, domain]
    for d in targets_for_ns:
        try:
            answers = await resolver.resolve(d, "NS")
            result["ns_records"] = [str(r.target).rstrip(".").lower() for r in answers]
            if result["ns_records"]:
                break
        except dns.resolver.NXDOMAIN:
            result["nxdomain"] = True
            break
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            continue
        except Exception as e:
            result["error"] = str(e)
            break

    # 2. If not already NXDOMAIN, query A and CNAME records
    if not result["nxdomain"]:
        try:
            answers = await resolver.resolve(domain, "A")
            result["a_records"] = [r.address for r in answers]
        except dns.resolver.NXDOMAIN:
            result["nxdomain"] = True
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            pass
        except Exception:
            pass

        try:
            answers = await resolver.resolve(domain, "CNAME")
            result["cname_records"] = [str(r.target).rstrip(".").lower() for r in answers]
        except Exception:
            pass

    _DNS_CACHE[domain] = (now, result)
    return result


def _check_ns_signals(ns_records: list[str]) -> Optional[DomainSignal]:
    """Check if any authoritative nameserver matches known parking/expired patterns."""
    for ns in ns_records:
        ns_lower = ns.lower()
        for pattern in PARKING_NS_PATTERNS:
            if pattern in ns_lower:
                return DomainSignal(
                    signal_type="ns_parking",
                    confidence=0.95,
                    detail=f"DNS服务器 '{ns}' 匹配停放/过期模式 '{pattern}'",
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
                        confidence=0.85,
                        detail=f"IP {ip_str} 在已知停放段 {network}",
                    )
        except ValueError:
            pass
    return None


async def detect_domain_status(
    url: str,
    expect_dns_server: Optional[str] = None,
    dns_timeout: float = 3.0,
) -> DomainVerdict:
    """Judge domain status purely based on authoritative DNS servers and records.

    Detection flow:
      1. Extract domain and resolve authoritative NS, A, CNAME records.
      2. If NXDOMAIN → domain expired/not found.
      3. If NS matches parking/expired patterns → domain expired/parked.
      4. If expect_dns_server is set and not matched → DNS server mismatch.
      5. If CNAME or IP matches parking ranges → domain parked.
      6. Otherwise → normal domain status.

    NO response content string matching is performed.
    """
    domain = _extract_domain(url)
    if not domain:
        return DomainVerdict(
            is_parked_or_expired=False,
            confidence=0.0,
            category="normal",
            summary="",
            dns_server=None,
            ns_records=[],
            signals=[],
        )

    try:
        dns_info = await _resolve_dns(domain, timeout=dns_timeout)
    except Exception as e:
        logger.debug(f"DNS resolution failed for {domain}: {e}")
        dns_info = {
            "domain": domain, "apex": domain,
            "a_records": [], "ns_records": [], "cname_records": [],
            "nxdomain": False, "no_nameservers": False, "error": str(e),
        }

    ns_records = dns_info.get("ns_records", [])
    dns_server_str = ", ".join(ns_records) if ns_records else None
    signals: list[DomainSignal] = []

    # 1. Check NXDOMAIN (domain deleted or expired from registry)
    if dns_info.get("nxdomain"):
        signals.append(DomainSignal(
            signal_type="dns_nxdomain",
            confidence=0.99,
            detail=f"域名 {domain} DNS 不存在 (NXDOMAIN)",
        ))
        return DomainVerdict(
            is_parked_or_expired=False,
            is_dns_not_found=True,
            confidence=0.99,
            category="domain_not_found",
            summary="DNS无法解析 (域名不存在/NXDOMAIN)",
            dns_server=None,
            ns_records=[],
            signals=signals,
        )

    # 2. Check NS Parking / Expired nameserver
    if ns_records:
        ns_signal = _check_ns_signals(ns_records)
        if ns_signal:
            signals.append(ns_signal)
            return DomainVerdict(
                is_parked_or_expired=True,
                confidence=0.95,
                category="domain_expired",
                summary=f"域名已过期或停放 ({ns_signal.detail})",
                dns_server=dns_server_str,
                ns_records=ns_records,
                signals=signals,
            )

    # 3. Check Expected DNS Server matching (if configured)
    if expect_dns_server and ns_records:
        expected_lower = expect_dns_server.strip().lower()
        if not any(expected_lower in ns for ns in ns_records):
            mismatch_signal = DomainSignal(
                signal_type="dns_mismatch",
                confidence=0.95,
                detail=f"当前DNS服务器: {dns_server_str}，预期包含: {expect_dns_server}",
            )
            signals.append(mismatch_signal)
            return DomainVerdict(
                is_parked_or_expired=True,
                confidence=0.95,
                category="dns_mismatch",
                summary=f"DNS服务器不匹配 (当前: {dns_server_str or '无'}, 预期包含: {expect_dns_server})",
                dns_server=dns_server_str,
                ns_records=ns_records,
                signals=signals,
            )

    # 4. Check CNAME parking
    if dns_info.get("cname_records"):
        cname_signal = _check_cname_signals(dns_info["cname_records"])
        if cname_signal:
            signals.append(cname_signal)
            return DomainVerdict(
                is_parked_or_expired=True,
                confidence=0.95,
                category="domain_parked",
                summary=f"域名解析指向停放服务 ({cname_signal.detail})",
                dns_server=dns_server_str,
                ns_records=ns_records,
                signals=signals,
            )

    # 5. Check IP parking
    if dns_info.get("a_records"):
        ip_signal = _check_ip_signals(dns_info["a_records"])
        if ip_signal:
            signals.append(ip_signal)
            return DomainVerdict(
                is_parked_or_expired=True,
                confidence=0.85,
                category="domain_parked",
                summary=f"域名解析指向停放IP ({ip_signal.detail})",
                dns_server=dns_server_str,
                ns_records=ns_records,
                signals=signals,
            )

    # 6. Check if NoNameservers / resolution error
    if dns_info.get("no_nameservers") or (not ns_records and dns_info.get("error")):
        err_detail = dns_info.get("error") or "无可用DNS服务器"
        signals.append(DomainSignal(
            signal_type="dns_error",
            confidence=0.80,
            detail=f"DNS服务器异常: {err_detail}",
        ))
        return DomainVerdict(
            is_parked_or_expired=False,
            is_dns_error=True,
            confidence=0.80,
            category="dns_error",
            summary=f"DNS解析失败 ({err_detail})",
            dns_server=dns_server_str,
            ns_records=ns_records,
            signals=signals,
        )

    # 7. Normal DNS status
    return DomainVerdict(
        is_parked_or_expired=False,
        confidence=0.0,
        category="normal",
        summary="",
        dns_server=dns_server_str,
        ns_records=ns_records,
        signals=[],
    )


def detect_domain_status_sync(
    url: str,
    expect_dns_server: Optional[str] = None,
    dns_timeout: float = 3.0,
) -> DomainVerdict:
    """Sync wrapper around detect_domain_status."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    detect_domain_status(url, expect_dns_server, dns_timeout),
                )
                return future.result(timeout=dns_timeout + 2)
        else:
            return loop.run_until_complete(
                detect_domain_status(url, expect_dns_server, dns_timeout)
            )
    except Exception as e:
        logger.debug(f"Sync domain detection failed: {e}")
        return DomainVerdict(
            is_parked_or_expired=False, confidence=0.0,
            category="normal", summary="", dns_server=None, ns_records=[], signals=[],
        )

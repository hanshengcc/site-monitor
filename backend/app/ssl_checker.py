"""SSL certificate checker — async TLS handshake with real verification.

For HTTPS targets that return HTTP 200, we do a *separate* TLS connection with
full certificate verification enabled to detect:
  - Expired certificate
  - Not-yet-valid certificate
  - Hostname mismatch
  - Self-signed / untrusted root
  - Certificate chain problems
  - Certificate expiring soon (warning)

The check result is returned as a dict that can be stored alongside the health
check result.  Certificate issues do NOT mark is_ok=False by default — they are
treated as warnings.  Only if the caller explicitly asks (or config says so) do
we fail the check.
"""
import asyncio
import ssl
import socket
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from loguru import logger


def _parse_cert_time(s: str) -> Optional[datetime]:
    """Parse OpenSSL time string like 'Jul  9 02:32:55 2026 GMT'."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_san(cert: dict) -> list[str]:
    """Extract Subject Alternative Names from a parsed certificate."""
    san = cert.get("subjectAltName", ())
    return [value for typ, value in san if typ == "DNS"]


def _extract_cn(cert: dict) -> str:
    """Extract Common Name from subject."""
    subject = cert.get("subject", ())
    for rdn in subject:
        for attr_type, attr_value in rdn:
            if attr_type == "commonName":
                return attr_value
    return ""


def _extract_issuer_cn(cert: dict) -> str:
    """Extract issuer Common Name."""
    issuer = cert.get("issuer", ())
    for rdn in issuer:
        for attr_type, attr_value in rdn:
            if attr_type in ("commonName", "organizationName"):
                return attr_value
    return ""


async def check_ssl_cert(
    host: str,
    port: int = 443,
    timeout: float = 10.0,
    warn_days: int = 30,
) -> dict:
    """Check SSL certificate for a host.

    Returns:
        {
            "ssl_checked": True,
            "ssl_valid": bool,         # True if cert passes all checks
            "ssl_error": str | None,   # Error description if invalid
            "ssl_issuer": str,         # Issuer CN/Org
            "ssl_subject": str,        # Subject CN
            "ssl_not_before": str,     # ISO datetime
            "ssl_not_after": str,      # ISO datetime
            "ssl_days_left": int,      # Days until expiry (negative if expired)
            "ssl_san": [str],          # Subject Alt Names
            "ssl_warning": str | None, # Non-fatal warning (e.g. expiring soon)
        }
    """
    result = {
        "ssl_checked": True,
        "ssl_valid": False,
        "ssl_error": None,
        "ssl_issuer": "",
        "ssl_subject": "",
        "ssl_not_before": None,
        "ssl_not_after": None,
        "ssl_days_left": None,
        "ssl_san": [],
        "ssl_warning": None,
    }

    # ---- Step 1: Try with FULL verification to detect issues ----
    ctx_strict = ssl.create_default_context()
    # check_hostname + CERT_REQUIRED is the default for create_default_context

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx_strict, server_hostname=host),
            timeout=timeout,
        )
        # Verification passed — extract cert info
        ssl_obj = writer.get_extra_info("ssl_object")
        cert = ssl_obj.getpeercert()
        writer.close()
        await writer.wait_closed()

        result["ssl_valid"] = True
        _fill_cert_info(result, cert, host, warn_days)

    except ssl.SSLCertVerificationError as e:
        # Cert invalid — still try to get cert details via non-verifying connection
        reason = getattr(e, "reason", str(e))
        msg = str(e)

        if "expired" in msg.lower():
            result["ssl_error"] = f"证书已过期: {reason}"
        elif "not yet valid" in msg.lower():
            result["ssl_error"] = f"证书尚未生效: {reason}"
        elif "hostname mismatch" in msg.lower() or "Hostname mismatch" in msg:
            result["ssl_error"] = f"证书域名不匹配: {reason}"
        elif "self-signed" in msg.lower():
            if "self-signed certificate in certificate chain" in msg.lower():
                result["ssl_error"] = f"证书链中包含自签名证书: {reason}"
            else:
                result["ssl_error"] = f"自签名证书: {reason}"
        elif "unable to get local issuer" in msg.lower():
            result["ssl_error"] = f"无法验证证书链 (不受信任的CA): {reason}"
        else:
            result["ssl_error"] = f"证书验证失败: {reason}"

        # Try to get cert details without verification
        await _try_get_cert_details(result, host, port, timeout, warn_days)

    except (asyncio.TimeoutError, TimeoutError):
        result["ssl_error"] = "TLS握手超时"
    except ConnectionRefusedError:
        result["ssl_error"] = "连接被拒绝 (端口未开放)"
    except OSError as e:
        result["ssl_error"] = f"连接失败: {str(e)[:120]}"
    except Exception as e:
        result["ssl_error"] = f"{type(e).__name__}: {str(e)[:120]}"

    return result


async def _try_get_cert_details(
    result: dict, host: str, port: int, timeout: float, warn_days: int,
):
    """Try to retrieve cert details without verification (for diagnostic info)."""
    ctx_lax = ssl.create_default_context()
    ctx_lax.check_hostname = False
    ctx_lax.verify_mode = ssl.CERT_NONE

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx_lax, server_hostname=host),
            timeout=timeout,
        )
        ssl_obj = writer.get_extra_info("ssl_object")
        # getpeercert(binary_form=False) returns {} when verify_mode=CERT_NONE
        # So we use getpeercert(binary_form=True) + parse with cryptography
        cert_der = ssl_obj.getpeercert(binary_form=True)
        writer.close()
        await writer.wait_closed()

        if cert_der:
            _fill_cert_from_der(result, cert_der, host, warn_days)

    except Exception:
        pass  # Best-effort; we already have the error from Step 1


def _fill_cert_info(result: dict, cert: dict, host: str, warn_days: int):
    """Fill result dict from a parsed cert (from getpeercert())."""
    result["ssl_subject"] = _extract_cn(cert)
    result["ssl_issuer"] = _extract_issuer_cn(cert)
    result["ssl_san"] = _extract_san(cert)

    not_before = _parse_cert_time(cert.get("notBefore", ""))
    not_after = _parse_cert_time(cert.get("notAfter", ""))

    if not_before:
        result["ssl_not_before"] = not_before.isoformat()
    if not_after:
        result["ssl_not_after"] = not_after.isoformat()
        now = datetime.now(timezone.utc)
        days_left = (not_after - now).days
        result["ssl_days_left"] = days_left
        if days_left <= 0:
            result["ssl_warning"] = f"证书已过期 ({abs(days_left)} 天前)"
        elif days_left <= warn_days:
            result["ssl_warning"] = f"证书即将过期 (剩余 {days_left} 天)"


def _fill_cert_from_der(result: dict, cert_der: bytes, host: str, warn_days: int):
    """Parse DER certificate bytes and fill result dict.

    Uses the `cryptography` library if available, otherwise falls back to
    basic ssl module parsing.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert = x509.load_der_x509_certificate(cert_der, default_backend())

        # Subject CN
        cn_attrs = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        result["ssl_subject"] = cn_attrs[0].value if cn_attrs else ""

        # Issuer
        issuer_cn = cert.issuer.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        issuer_org = cert.issuer.get_attributes_for_oid(x509.oid.NameOID.ORGANIZATION_NAME)
        result["ssl_issuer"] = (
            issuer_cn[0].value if issuer_cn
            else issuer_org[0].value if issuer_org
            else ""
        )

        # Dates
        # cryptography >= 42.0 uses not_valid_before_utc / not_valid_after_utc
        not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=timezone.utc)
        not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)

        result["ssl_not_before"] = not_before.isoformat()
        result["ssl_not_after"] = not_after.isoformat()

        now = datetime.now(timezone.utc)
        days_left = (not_after - now).days
        result["ssl_days_left"] = days_left
        if days_left <= 0:
            result["ssl_warning"] = f"证书已过期 ({abs(days_left)} 天前)"
        elif days_left <= warn_days:
            result["ssl_warning"] = f"证书即将过期 (剩余 {days_left} 天)"

        # SAN
        try:
            san_ext = cert.extensions.get_extension_for_oid(
                x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            )
            result["ssl_san"] = san_ext.value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            result["ssl_san"] = []

    except ImportError:
        # cryptography not installed — can't parse DER further
        pass
    except Exception as e:
        logger.debug(f"DER cert parse failed: {e}")


async def check_ssl_for_url(
    url: str,
    timeout: float = 10.0,
    warn_days: int = 30,
) -> Optional[dict]:
    """Convenience wrapper: extract host from URL and check SSL.

    Returns None for non-HTTPS URLs.
    """
    if not url.startswith("https://"):
        return None

    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 443

    if not host:
        return None

    return await check_ssl_cert(host, port, timeout=timeout, warn_days=warn_days)

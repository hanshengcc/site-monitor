import asyncio
import time
from unittest.mock import patch
from backend.app.domain_detector import (
    get_apex_domain,
    detect_domain_status,
    _DNS_CACHE,
    _check_ns_signals,
    _check_cname_signals,
    _check_ip_signals,
)


def test_get_apex_domain():
    assert get_apex_domain("example.com") == "example.com"
    assert get_apex_domain("sub.example.com") == "example.com"
    assert get_apex_domain("deep.nested.sub.example.com") == "example.com"
    assert get_apex_domain("api.site.com.cn") == "site.com.cn"
    assert get_apex_domain("test.co.uk") == "test.co.uk"
    assert get_apex_domain("192.168.1.1") == "192.168.1.1"
    assert get_apex_domain("localhost") == "localhost"


def test_detect_domain_nxdomain():
    _DNS_CACHE.clear()
    fake_dns_info = {
        "domain": "nonexistent-domain-test123.com",
        "apex": "nonexistent-domain-test123.com",
        "a_records": [],
        "ns_records": [],
        "cname_records": [],
        "nxdomain": True,
        "no_nameservers": False,
        "error": None,
    }
    with patch("backend.app.domain_detector._resolve_dns", return_value=fake_dns_info):
        verdict = asyncio.run(detect_domain_status("https://nonexistent-domain-test123.com"))
        assert verdict.is_dns_not_found is True
        assert verdict.category == "domain_not_found"
        assert "NXDOMAIN" in verdict.summary
        assert verdict.dns_server is None


def test_detect_domain_parked_ns():
    _DNS_CACHE.clear()
    fake_dns_info = {
        "domain": "parked-test.com",
        "apex": "parked-test.com",
        "a_records": ["1.2.3.4"],
        "ns_records": ["ns1.sedoparking.com", "ns2.sedoparking.com"],
        "cname_records": [],
        "nxdomain": False,
        "no_nameservers": False,
        "error": None,
    }
    with patch("backend.app.domain_detector._resolve_dns", return_value=fake_dns_info):
        verdict = asyncio.run(detect_domain_status("https://parked-test.com"))
        assert verdict.is_parked_or_expired is True
        assert verdict.category == "domain_expired"
        assert "sedoparking" in verdict.summary
        assert "ns1.sedoparking.com" in verdict.dns_server


def test_detect_domain_expected_dns_match():
    _DNS_CACHE.clear()
    fake_dns_info = {
        "domain": "my-good-site.com",
        "apex": "my-good-site.com",
        "a_records": ["1.2.3.4"],
        "ns_records": ["f1g1ns1.dnspod.net", "f1g1ns2.dnspod.net"],
        "cname_records": [],
        "nxdomain": False,
        "no_nameservers": False,
        "error": None,
    }
    # Note: dnspod.net is in PARKING_NS_PATTERNS if f1g1ns1, let's use alidns
    fake_dns_info["ns_records"] = ["ns1.alidns.com", "ns2.alidns.com"]
    with patch("backend.app.domain_detector._resolve_dns", return_value=fake_dns_info):
        verdict = asyncio.run(detect_domain_status("https://my-good-site.com", expect_dns_server="alidns.com"))
        assert verdict.is_parked_or_expired is False
        assert verdict.category == "normal"
        assert verdict.summary == ""
        assert "alidns.com" in verdict.dns_server


def test_detect_domain_expected_dns_mismatch():
    _DNS_CACHE.clear()
    fake_dns_info = {
        "domain": "my-good-site.com",
        "apex": "my-good-site.com",
        "a_records": ["1.2.3.4"],
        "ns_records": ["ns1.alidns.com", "ns2.alidns.com"],
        "cname_records": [],
        "nxdomain": False,
        "no_nameservers": False,
        "error": None,
    }
    with patch("backend.app.domain_detector._resolve_dns", return_value=fake_dns_info):
        verdict = asyncio.run(detect_domain_status("https://my-good-site.com", expect_dns_server="cloudflare.com"))
        assert verdict.is_parked_or_expired is True
        assert verdict.category == "dns_mismatch"
        assert "DNS服务器不匹配" in verdict.summary
        assert "ns1.alidns.com" in verdict.dns_server
        assert "cloudflare.com" in verdict.summary


def test_detect_domain_parked_ip():
    _DNS_CACHE.clear()
    fake_dns_info = {
        "domain": "some-ip-parked.com",
        "apex": "some-ip-parked.com",
        "a_records": ["34.102.136.180"],
        "ns_records": ["ns1.exampledns.com"],
        "cname_records": [],
        "nxdomain": False,
        "no_nameservers": False,
        "error": None,
    }
    with patch("backend.app.domain_detector._resolve_dns", return_value=fake_dns_info):
        verdict = asyncio.run(detect_domain_status("https://some-ip-parked.com"))
        assert verdict.is_parked_or_expired is True
        assert verdict.category == "domain_parked"
        assert "停放IP" in verdict.summary


def test_signals_checkers():
    # NS signal check
    ns_sig = _check_ns_signals(["ns1.sedoparking.com"])
    assert ns_sig is not None
    assert ns_sig.signal_type == "ns_parking"

    # Clean NS check
    assert _check_ns_signals(["ns1.google.com"]) is None

    # CNAME signal check
    cname_sig = _check_cname_signals(["parking.bodis.com"])
    assert cname_sig is not None
    assert cname_sig.signal_type == "cname_parking"

    # IP signal check
    ip_sig = _check_ip_signals(["34.102.136.180"])
    assert ip_sig is not None
    assert ip_sig.signal_type == "ip_parking"


def test_dns_cache_expiration():
    _DNS_CACHE.clear()
    fake_data = {"test": 123}
    _DNS_CACHE["test.com"] = (time.monotonic(), fake_data)
    # Immediate read should hit cache
    from backend.app.domain_detector import _resolve_dns
    cached = asyncio.run(_resolve_dns("test.com"))
    assert cached == fake_data

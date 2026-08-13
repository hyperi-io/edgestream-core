import pytest
from conftest import wait_for_nameservers, resolves_ok

pytestmark = pytest.mark.dns

DNS1_YAML = """
version: 1
networks:
  dns:
    - id: 1
      ip_address: 192.168.1.1
      port: 53
"""

DNS_FAIL_YAML = """
version: 1
networks:
  dns:
    - id: 1
      ip_address: 10.1.1.1
      port: 53
"""

DNS_MULTI_YAML = """
version: 1
networks:
  dns:
    - id: 1
      ip_address: 8.8.8.8
      port: 53
    - id: 2
      ip_address: 1.1.1.1
      port: 53
"""

def _assert_contains_all(actual_ips, expected_ips):
    aset, eset = set(actual_ips), set(expected_ips)
    missing = sorted(list(eset - aset))
    assert not missing, f"Upstreams missing {missing}; got {actual_ips}, expected to include {expected_ips}"

def test_dns_settings_sequence(host, apply_module_config, dns_domains):
    """Apply three DNS configs and verify behavior each time."""
    success_domain = dns_domains["success"]
    fail_domain = dns_domains["fail"]

    # Apply working DNS and expect success
    apply_module_config("dns-settings", DNS1_YAML)
    ups = wait_for_dnsmasq_upstreams(host, ["192.168.1.1"])
    _assert_contains_all(ups, ["192.168.1.1"])
    assert resolves_ok(host, success_domain, timeout=15), f"Expected {success_domain} to resolve; upstreams now {ups}"

    # Apply a bad/unreachable DNS and expect resolution failure
    apply_module_config("dns-settings", DNS_FAIL_YAML)
    ups = wait_for_dnsmasq_upstreams(host, ["10.1.1.1"])
    _assert_contains_all(ups, ["10.1.1.1"])
    assert not resolves_ok(host, fail_domain, timeout=10), f"Expected resolution to fail with upstream {ups}"

    # Apply two public resolvers and expect success, both set
    apply_module_config("dns-settings", DNS_MULTI_YAML)
    ups = wait_for_dnsmasq_upstreams(host, ["8.8.8.8", "1.1.1.1"])
    _assert_contains_all(ups, ["8.8.8.8", "1.1.1.1"])
    assert resolves_ok(host, success_domain, timeout=15), f"Expected resolution to succeed with upstreams {ups}"
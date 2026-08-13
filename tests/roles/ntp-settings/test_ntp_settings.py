import pytest
from conftest import (
    wait_for_chrony_config_sources,
    wait_for_chronyc_sources,
    chrony_sources_from_file,
    chrony_service,
)

pytestmark = pytest.mark.ntp

NTP1_YAML = """
version: 1
networks:
  ntp:
    - 1.au.pool.ntp.org
"""

NTP_FAIL_YAML = """
version: 1
networks:
  ntp:
    - 10.1.1.1
"""

NTP_MULTI_YAML = """
version: 1
networks:
  ntp:
    - 0.au.pool.ntp.org
    - 1.au.pool.ntp.org
"""

def _assert_contains_all(actual, expected, label="sources"):
    aset, eset = set(actual), set(expected)
    missing = sorted(list(eset - aset))
    assert not missing, f"{label} missing {missing}; got {actual}, expected to include {expected}"

def test_ntp_settings_sequence(host, apply_module_config):
    """
    Apply three NTP configs via --module ntp-settings and verify chrony config/service each time.
    """
    apply_module_config("ntp-settings", NTP1_YAML)

    cfg_sources = wait_for_chrony_config_sources(host, ["1.au.pool.ntp.org"])
    _assert_contains_all(cfg_sources, ["1.au.pool.ntp.org"], label="chrony config")

    # Chrony should be enabled and running (task 1.2)
    svc = chrony_service(host)
    assert svc.is_enabled, "chrony should be enabled"
    assert svc.is_running, "chrony should be running"

    runtime_sources = wait_for_chronyc_sources(host, ["1.au.pool.ntp.org"])
    if runtime_sources:
        _assert_contains_all(runtime_sources, ["1.au.pool.ntp.org"], label="chronyc sources")

    # Apply an unreachable server and ensure config reflects it
    apply_module_config("ntp-settings", NTP_FAIL_YAML)
    cfg_sources = wait_for_chrony_config_sources(host, ["10.1.1.1"])
    _assert_contains_all(cfg_sources, ["10.1.1.1"], label="chrony config (unreachable)")

    # Apply two public resolvers and ensure both appear
    apply_module_config("ntp-settings", NTP_MULTI_YAML)
    cfg_sources = wait_for_chrony_config_sources(host, ["0.au.pool.ntp.org", "1.au.pool.ntp.org"])
    _assert_contains_all(cfg_sources, ["0.au.pool.ntp.org", "1.au.pool.ntp.org"], label="chrony config (multi)")

    runtime_sources = wait_for_chronyc_sources(host, ["0.au.pool.ntp.org", "1.au.pool.ntp.org"])
    if runtime_sources:
        _assert_contains_all(runtime_sources, ["0.au.pool.ntp.org", "1.au.pool.ntp.org"], label="chronyc sources (multi)")

def test_ntp_removes_defaults(host, apply_module_config):
    """
    Task 1.3 removes default pool/server source files; verify they're gone after apply.
    """
    apply_module_config("ntp-settings", NTP1_YAML)
    assert not host.file("/etc/chrony/sources.d/pool.source").exists, "pool.source should be removed"
    assert not host.file("/etc/chrony/sources.d/server.source").exists, "server.source should be removed"

def test_ntp_config_file_permissions(host, apply_module_config):
    """
    Verify owner/group/mode per task 1.1 template install.
    """
    apply_module_config("ntp-settings", NTP1_YAML)
    f = host.file("/etc/chrony/sources.d/99-edgestream.sources")
    assert f.exists, "chrony sources file must exist"
    assert f.user == "root" and f.group == "root", "chrony sources file should be root:root"
    assert f.mode == 0o644, "chrony sources file mode should be 0644"

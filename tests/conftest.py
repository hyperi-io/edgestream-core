import os
import time
import pytest
import shlex
import re


EDGESTREAM_TASK_BIN = os.environ.get(
    "EDGESTREAM_TASK_BIN",
    "/opt/edgestream-core/bin/edgestream_task"
)

DNS_RESOLVE_DOMAIN_SUCCESS = os.environ.get("DNS_RESOLVE_DOMAIN_SUCCESS", "www.google.com")
DNS_RESOLVE_DOMAIN_FAIL = os.environ.get("DNS_RESOLVE_DOMAIN_FAIL", DNS_RESOLVE_DOMAIN_SUCCESS)
TEST_DNS_APPLY_TIMEOUT = int(os.environ.get("TEST_DNS_APPLY_TIMEOUT", "30"))

def _has_cmd(host, cmd):
    return host.run(f"command -v {cmd}").rc == 0

def effective_nameservers(host):
    """Return a list of effective nameserver IPs (strings)."""
    ns = []
    # Prefer resolvectl (systemd-resolved)
    if _has_cmd(host, "resolvectl"):
        out = host.check_output("resolvectl status || true")
        for line in out.splitlines():
            line = line.strip()
            if line.startswith(("DNS Servers:", "DNS Server:", "Current DNS Server:")):
                parts = line.split(":", 1)[1].strip().split()
                for p in parts:
                    if p and all(ch.isdigit() or ch in ".:" for ch in p):
                        ns.append(p.split("%")[0])
    # Fallback: resolv.conf
    if not ns:
        rf = host.file("/etc/resolv.conf")
        if rf.exists:
            for line in rf.content_string.splitlines():
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        ns.append(parts[1])
    # Deduplicate preserving order
    seen, out = set(), []
    for x in ns:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def wait_for_nameservers(host, expect, timeout=TEST_DNS_APPLY_TIMEOUT):
    """Wait until nameservers equal or include 'expect' (order-insensitive)."""
    expect_set = set(expect)
    last = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = effective_nameservers(host)
        last = cur
        cur_set = set(i.split("%")[0] for i in cur)
        if cur_set == expect_set or expect_set.issubset(cur_set):
            return cur
        time.sleep(1)
    return last

def resolves_ok(host, domain, timeout=10):
    """Return True if hostname resolves via getent within timeout seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = host.run(f"getent hosts {domain}")
        if r.rc == 0 and r.stdout.strip():
            return True
        time.sleep(1)
    return False

def _dedup(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out

def dnsmasq_upstreams(host, conf_path="/etc/dnsmasq.d/edgestream.conf"):
    """
    Return the upstream DNS server IPs configured in dnsmasq (server=… lines),
    e.g. ['10.1.1.1'] or ['8.8.8.8','1.1.1.1'].
    """
    ups = []
    f = host.file(conf_path)
    if f.exists:
        for line in f.content_string.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("server="):
                # server=<host> or server=<host>#<port> or with optional @/%
                val = s.split("=", 1)[1].strip()
                val = val.split("#", 1)[0]  # drop :port/#port if present
                val = val.split("@", 1)[0]  # drop @interface if present
                val = val.split("%", 1)[0]  # drop %zone if present
                val = val.strip("[]")       # drop brackets for IPv6 literal if any
                if val:
                    ups.append(val)
    return _dedup(ups)

def wait_for_dnsmasq_upstreams(host, expect, timeout=TEST_DNS_APPLY_TIMEOUT, conf_path="/etc/dnsmasq.d/edgestream.conf"):
    """
    Wait until dnsmasq upstream list (from server= lines) equals or includes 'expect'.
    """
    expect_set = set(expect)
    last = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = dnsmasq_upstreams(host, conf_path=conf_path)
        last = cur
        cur_set = set(cur)
        if cur_set == expect_set or expect_set.issubset(cur_set):
            return cur
        time.sleep(1)
    return last

@pytest.fixture
def apply_module_config(host, tmp_path):
    """
    Write YAML to <tmp>/cfg/test_case.yaml, export EDGESTREAM_SETTINGS_FILE,
    run edgestream_task --module <role>, and echo details for debugging.
    """
    def _apply(module_name: str, yaml_text: str):
        cfgdir = tmp_path / "cfg"
        cfgdir.mkdir(parents=True, exist_ok=True)

        cfg = (cfgdir / "test_case.yaml").resolve()
        cfg.write_text(yaml_text)

        # Debug: show YAML content
        print("\n[apply_module_config] Writing YAML to:", cfg)
        print("[apply_module_config] YAML contents:\n" + yaml_text)

        cmd = (
            f"EDGESTREAM_SETTINGS_FILE={shlex.quote(str(cfg))} "
            f"{shlex.quote(EDGESTREAM_TASK_BIN)} --module {shlex.quote(module_name)}"
        )

        # Debug: show command
        print("[apply_module_config] Running command:", cmd)

        r = host.run(cmd)

        # Debug: show result
        print(f"[apply_module_config] Return code: {r.rc}")
        if r.stdout:
            print("[apply_module_config] STDOUT:\n" + r.stdout)
        if r.stderr:
            print("[apply_module_config] STDERR:\n" + r.stderr)

        assert r.rc == 0, (
            f"--module {module_name} failed: rc={r.rc} stdout={r.stdout} stderr={r.stderr}"
        )
        return str(cfg)

    return _apply

@pytest.fixture
def dns_domains():
    return {
        "success": os.environ.get("DNS_RESOLVE_DOMAIN_SUCCESS", "www.google.com"),
        "fail": os.environ.get("DNS_RESOLVE_DOMAIN_FAIL", os.environ.get("DNS_RESOLVE_DOMAIN_SUCCESS", "www.google.com")),
    }

def chrony_sources_from_file(host, path="/etc/chrony/sources.d/99-edgestream.sources"):
    """
    Parse chrony sources file and return a de-duplicated list of sources
    from 'server', 'pool', or 'peer' lines.
    """
    f = host.file(path)
    srcs = []
    if f.exists:
        for line in f.content_string.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith(("server ", "pool ", "peer ")):
                # server <host> [options...]
                parts = s.split()
                if len(parts) >= 2:
                    hostspec = parts[1].strip("[]")  # strip IPv6 brackets
                    hostspec = hostspec.split("#", 1)[0]
                    srcs.append(hostspec)
    # dedupe preserve order
    seen, out = set(), []
    for x in srcs:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def wait_for_chrony_config_sources(host, expect, timeout=30, path="/etc/chrony/sources.d/99-edgestream.sources"):
    """
    Wait until the chrony sources file contains all expected sources.
    """
    deadline = time.time() + timeout
    last = []
    eset = set(expect)
    while time.time() < deadline:
        cur = chrony_sources_from_file(host, path=path)
        last = cur
        if eset.issubset(set(cur)):
            return cur
        time.sleep(1)
    return last

def chronyc_sources(host):
    """
    Return list of sources reported by 'chronyc -n sources' (requires chronyc).
    Name/IP column is parsed; returns e.g. ['192.168.1.1', '10.1.1.1'].
    """
    r = host.run("chronyc -n sources")
    if r.rc != 0:
        return []
    srcs = []
    for line in r.stdout.splitlines():
        # Lines look like: "^* 192.0.2.10 ...", "^? 10.1.1.1 ..."
        m = re.match(r"^[\^\?\+\*-]\s+(\S+)\s+", line)
        if m:
            srcs.append(m.group(1))
    # dedupe preserve order
    seen, out = set(), []
    for x in srcs:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def wait_for_chronyc_sources(host, expect, timeout=30):
    """
    Wait until 'chronyc -n sources' lists at least the expected sources.
    If chronyc is not present or returns error, returns [] quickly.
    """
    if host.run("command -v chronyc").rc != 0:
        return []
    deadline = time.time() + timeout
    last = []
    eset = set(expect)
    while time.time() < deadline:
        cur = chronyc_sources(host)
        last = cur
        if eset.issubset(set(cur)):
            return cur
        time.sleep(1)
    return last

def chrony_service(host):
    """
    Return testinfra service handle for chrony (Debian/Ubuntu name is 'chrony').
    """
    return host.service("chrony")
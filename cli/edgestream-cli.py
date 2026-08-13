#!/usr/bin/env python3
# edgestream CLI (reads secrets file & auto-sends token)
import os, sys, time, socket, argparse, pprint
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from dotenv import load_dotenv
import pyotp
import pyqrcode

# -----------------------------
# Files & defaults
# -----------------------------
SECRETS_FILE = "/etc/edgestream/edgestream-api.secrets"
DEFAULTS_FILE = "/etc/default/edgestream-api"

def read_secrets(path: str = SECRETS_FILE) -> dict:
    vals = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return vals

# Load .env layers (non-fatal if missing)
if Path(SECRETS_FILE).is_file():
    load_dotenv(SECRETS_FILE)
if Path(DEFAULTS_FILE).is_file():
    load_dotenv(DEFAULTS_FILE)
_local_env = Path(__file__).with_name(".env")
if _local_env.exists():
    load_dotenv(_local_env)

SECRETS = read_secrets()

# Base URL may be "http://127.0.0.1:3001" OR ".../api/v1"
RAW_BASE = (
    os.environ.get("EDGESTREAM_BASE_URL")
    or SECRETS.get("EDGESTREAM_BASE_URL")
    or os.environ.get("URL")
    or "http://127.0.0.1:3001"
)

def _normalize_host_root(url: str) -> str:
    u = url.rstrip("/")
    if u.endswith("/api/v1"):
        u = u[:-7]
    return u

HOST_ROOT = _normalize_host_root(RAW_BASE)
API_PREFIX = "/api/v1"

# TLS verify / CA file / timeout
def _env_bool(v, default=True):
    if v is None:
        return default
    return str(v).lower() not in {"0", "false", "no", "off"}

VERIFY = _env_bool(os.environ.get("EDGESTREAM_TLS_VERIFY", SECRETS.get("EDGESTREAM_TLS_VERIFY", "true")), True)
CA_FILE = os.environ.get("EDGESTREAM_CA_FILE") or SECRETS.get("EDGESTREAM_CA_FILE")
TIMEOUT = float(os.environ.get("EDGESTREAM_HTTP_TIMEOUT") or SECRETS.get("EDGESTREAM_HTTP_TIMEOUT") or "60")

# Auth controls
AUTH_MODE = (os.environ.get("EDGESTREAM_AUTH_MODE") or SECRETS.get("EDGESTREAM_AUTH_MODE") or "auto").lower()
# Prefer explicit bearer, else CLI token for bearer; allow API key too
ENV_BEARER = (
    os.environ.get("EDGESTREAM_TOKEN")
    or SECRETS.get("EDGESTREAM_TOKEN")
    or os.environ.get("CLI_AUTH_TOKEN")
    or SECRETS.get("CLI_AUTH_TOKEN")
)
ENV_API_KEY = os.environ.get("EDGESTREAM_API_KEY") or SECRETS.get("EDGESTREAM_API_KEY")

# -----------------------------
# HTTP helpers
# -----------------------------
SESSION = requests.Session()

def _is_localhost(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in {"127.0.0.1", "localhost", "::1"}
    except Exception:
        return False

def _auth_headers() -> dict:
    """
    Auto mode: if we have ANY credential, use it (even on localhost),
    so protected endpoints (/usermgmt/*) work without extra env.
    Set EDGESTREAM_AUTH_MODE=none to force no auth header.
    """
    if AUTH_MODE == "none":
        return {}
    if AUTH_MODE == "bearer":
        if not ENV_BEARER:
            raise SystemExit("EDGESTREAM_AUTH_MODE=bearer but no token found in secrets/env")
        return {"Authorization": f"Bearer {ENV_BEARER}"}
    if AUTH_MODE == "api-key":
        if not ENV_API_KEY and not ENV_BEARER:
            raise SystemExit("EDGESTREAM_AUTH_MODE=api-key but no API key found in secrets/env")
        return {"X-CLI-AUTH": (ENV_API_KEY or ENV_BEARER)}
    # auto:
    if ENV_BEARER:
        return {"Authorization": f"Bearer {ENV_BEARER}"}
    if ENV_API_KEY:
        return {"X-CLI-AUTH": ENV_API_KEY}
    # last resort: no header
    return {}

def _verify_arg():
    if CA_FILE:
        return CA_FILE
    return VERIFY

def _url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith(API_PREFIX + "/") and path != API_PREFIX:
        path = API_PREFIX + path
    return HOST_ROOT.rstrip("/") + path

def _handle_conn_err(exc):
    print(f"Error: {exc}")
    sys.exit(1)

def GET(path: str, **kwargs):
    try:
        headers = {**_auth_headers(), **kwargs.pop("headers", {})}
        return SESSION.get(_url(path), headers=headers, verify=_verify_arg(), timeout=TIMEOUT, **kwargs)
    except requests.exceptions.ConnectionError as exc:
        _handle_conn_err(exc)

def POST(path: str, **kwargs):
    try:
        headers = {**_auth_headers(), **kwargs.pop("headers", {})}
        return SESSION.post(_url(path), headers=headers, verify=_verify_arg(), timeout=TIMEOUT, **kwargs)
    except requests.exceptions.ConnectionError as exc:
        _handle_conn_err(exc)

def PUT(path: str, **kwargs):
    try:
        headers = {**_auth_headers(), **kwargs.pop("headers", {})}
        return SESSION.put(_url(path), headers=headers, verify=_verify_arg(), timeout=TIMEOUT, **kwargs)
    except requests.exceptions.ConnectionError as exc:
        _handle_conn_err(exc)

def DELETE(path: str, **kwargs):
    try:
        headers = {**_auth_headers(), **kwargs.pop("headers", {})}
        return SESSION.delete(_url(path), headers=headers, verify=_verify_arg(), timeout=TIMEOUT, **kwargs)
    except requests.exceptions.ConnectionError as exc:
        _handle_conn_err(exc)

# -----------------------------
# Privilege check (your original)
# -----------------------------
def check_privileges():
    if not os.environ.get("SUDO_UID") and os.geteuid() != 0:
        raise PermissionError("You need to run this script with sudo or as root.")

# -----------------------------
# System endpoints
# -----------------------------
def write_config():
    r = GET("/hypercloud/export")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    print("Configuration generated")

def display_config():
    r = GET("/hypercloud/export")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    print(r.text)

def restore_config(config_filename: str):
    try:
        open(config_filename, "r").close()
    except Exception as e:
        print(f"Error loading or parsing configuration file -> {e}")
        sys.exit(1)
    try:
        files = {"config_yaml": ("config_yaml", open(config_filename, "rb"), "application/octet-stream")}
        r = POST("/hypercloud/restore", files=files, timeout=600)
        if r.status_code != 200:
            print(f"Error restoring configuration -> {r.text}")
            sys.exit(1)
        time.sleep(120)
        print("Restoration in process, see edgestream gateway for status")
    except Exception as e:
        print(f"Error restoring configuration to edgestream gateway -> {e}")
        sys.exit(1)

def install_config(config_filename: str):
    try:
        open(config_filename, "r").close()
    except Exception as e:
        print(f"Error loading or parsing configuration file -> {e}")
        sys.exit(1)
    try:
        files = {"config_yaml": ("config_yaml", open(config_filename, "rb"), "application/octet-stream")}
        r = POST("/hypercloud/install", files=files, timeout=600)
        if r.status_code != 200:
            print(f"Error installing configuration -> {r.text}")
            sys.exit(1)
        print("Configuration in process")
        job_id = r.json().get("identifier")
        i = 0
        time.sleep(30)
        while i < 10:
            r2 = GET(f"/status/id/{job_id}")
            job_state = r2.json().get("state")
            if job_state in {"complete", "failed"}:
                print(f"Job {job_id} finished: {job_state}")
                break
            i += 1
            time.sleep(30)
    except Exception as e:
        print(f"Error installing configuration to edgestream gateway -> {e}")
        sys.exit(1)

def get_network():
    r = GET("/networks/ip-mgmt")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    print(r.json())

def get_org_id():
    r = GET("/system/org_id")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    return (r.json().get("org_id") or "").encode("ascii", "ignore").decode("utf-8")

def set_org_id(org_id: str):
    r = PUT("/system/org_id", json={"org_id": org_id})
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    return (r.json().get("org_id") or "").encode("ascii", "ignore").decode("utf-8")

def get_site_id():
    r = GET("/system/site_id")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    return (r.json().get("site_id") or "").encode("ascii", "ignore").decode("utf-8")

def set_site_id(site_id: str):
    r = PUT("/system/site_id", json={"site_id": site_id})
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    return (r.json().get("site_id") or "").encode("ascii", "ignore").decode("utf-8")

def get_interface_list():
    r = GET("/system")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    intf_list = [i["device"] for i in r.json().get("interfaces", [])]
    return " ".join(sorted(set(intf_list)))

def get_dns():
    r = GET("/networks/dns")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    dns_obj = r.json().get("result", [])
    dns_list = [f"{i['ip_address']}:{i['port']}" for i in dns_obj]
    unique = sorted(set(dns_list))
    final = []
    for i in unique:
        final.append(i)
        final.append(i)
    return " ".join(final)

# -----------------------------
# Users
# -----------------------------
def list_pending_users():
    r = GET("/usermgmt/users/pending")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    if users:
        print("Pending users:")
        for u in users:
            print(f"\tEmail: {u.get('email')}")
            print(f"\tName: {u.get('full_name')} {u.get('display_name')}\n")

def list_users():
    r = GET("/usermgmt/users/approved")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    if users:
        print("Authorized users:")
        for u in users:
            print(f"\tEmail: {u.get('email')}")
            print(f"\tName: {u.get('full_name')} {u.get('display_name')}")
            print("\tMFA:", "Enabled" if u.get("otp_secret") else "Disabled")
            print("")

def enable_user(email: str, action: bool):
    r = GET("/usermgmt/users")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    target = next((u for u in users if u.get("email") == email), None)
    if not target:
        print(f"Error retrieving user {email}")
        sys.exit(1)
    r = PUT("/usermgmt/users", params={"email": email}, json={"is_approved": action})
    if r.status_code != 200:
        print(f"Error updating account -> {r.text}")
        sys.exit(1)
    print("Account enabled" if action else "Account disabled")

def change_passwd(user_email: str, new_password: str, cli_token: str):
    r = GET("/usermgmt/users")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    target = next((u for u in users if u.get("email") == user_email), None)
    if not target:
        print(f"Error retrieving user {user_email}")
        sys.exit(1)
    params = {"email": user_email, "current_password": cli_token, "new_password": new_password}
    r = PUT("/usermgmt/password", params=params)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        print(f"Error updating password for user {user_email} -> {detail}")
        sys.exit(1)
    print(r.json().get("result"))

def gen_otp(email: str):
    r = GET("/usermgmt/users")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    target = next((u for u in users if u.get("email") == email), None)
    if not target:
        print(f"Error retrieving user {email}")
        sys.exit(1)
    new_otp = pyotp.random_base32()
    r = PUT("/usermgmt/users", params={"email": email}, json={"otp_secret": new_otp})
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        print(f"Error updating authenticator code -> {detail}")
        sys.exit(1)
    label = f"{socket.getfqdn()}@edgestream"
    otp_url = pyotp.totp.TOTP(new_otp).provisioning_uri(name=email, issuer_name=label)
    qr_code = pyqrcode.create(otp_url)
    print(otp_url)
    print(qr_code.terminal(quiet_zone=1))

def display_otp(email: str):
    r = GET("/usermgmt/users")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    target = next((u for u in users if u.get("email") == email), None)
    if not target:
        print(f"Error retrieving user {email}")
        sys.exit(1)
    otp_secret = target.get("otp_secret") or ""
    if not otp_secret:
        print(f"No otp secret is set for user {email}")
        return
    label = f"{socket.getfqdn()}@edgestream"
    otp_url = pyotp.totp.TOTP(otp_secret).provisioning_uri(name=email, issuer_name=label)
    qr_code = pyqrcode.create(otp_url)
    print(otp_url)
    print(qr_code.terminal(quiet_zone=1))

def delete_otp(email: str):
    r = GET("/usermgmt/users")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    users = r.json().get("users", [])
    target = next((u for u in users if u.get("email") == email), None)
    if not target:
        print(f"Error retrieving user {email}")
        sys.exit(1)
    otp_secret = target.get("otp_secret") or ""
    if not otp_secret:
        print(f"No otp secret is set for user {email}")
        return
    r = PUT("/usermgmt/users", params={"email": email}, json={"otp_secret": ""})
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        print(f"Error updating authenticator code -> {detail}")
        sys.exit(1)
    print(f"Otp secret removed for user {email}")

# -----------------------------
# Networks
# -----------------------------
def get_ntp():
    r = GET("/networks/ntps")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    ntp_obj = r.json().get("ip_addresses", [])
    ntp_list = [f"{i['ip_address']}:{i['port']}" for i in ntp_obj]
    unique = sorted(set(ntp_list))
    final = []
    for i in unique:
        final.append(i)
        final.append(i)
    return " ".join(final)

def set_dns(dns_servers: str):
    temp_servers = list(set((dns_servers or "").split(",")))
    unique = [s if ":" in s else f"{s}:53" for s in temp_servers if s]
    r = GET("/networks/dns")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    saved = [f"{i['ip_address']}:{i['port']}" for i in r.json().get("result", [])]
    for i in sorted(set(unique) - set(saved)):
        ip, port = i.split(":")
        data = {"hostnames": [{"ip_address": ip, "port": port}]}
        POST("/networks/dns", json=data)
    for i in sorted(set(saved) - set(unique)):
        ip = i.split(":")[0]
        DELETE("/networks/dns", params={"ip_address": ip})

def set_ntp(ntp_servers: str):
    temp_servers = list(set((ntp_servers or "").split(",")))
    unique = [s if ":" in s else f"{s}:123" for s in temp_servers if s]
    r = GET("/networks/ntps")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    saved = [f"{i['ip_address']}:{i['port']}" for i in r.json().get("ip_addresses", [])]
    for i in sorted(set(unique) - set(saved)):
        ip, port = i.split(":")
        data = {"ip_addresses": [{"ip_address": ip, "port": port}]}
        POST("/networks/ntp", json=data)
    for i in sorted(set(saved) - set(unique)):
        ip = i.split(":")[0]
        DELETE("/networks/ntp", params={"ip_address": ip})

def del_dns():
    r = GET("/networks/dns")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    for i in r.json().get("result", []):
        DELETE("/networks/dns", params={"ip_address": i["ip_address"]})

def del_ntp():
    r = GET("/networks/ntps")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    for i in r.json().get("ip_addresses", []):
        DELETE("/networks/ntp", params={"ip_address": i["ip_address"]})

def get_mgmt_intf():
    r = GET("/networks/ip-mgmt")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    for i in r.json().get("ip_management", []):
        if i.get("type") == "mgmt":
            print(i.get("iface"), i.get("type"), i.get("family"), i.get("ip_address"), i.get("netmask"), i.get("gateway"))

def get_event_intf():
    r = GET("/networks/ip-mgmt")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    for i in r.json().get("ip_management", []):
        if i.get("type") == "event":
            print(i.get("iface"), i.get("type"), i.get("family"), i.get("ip_address"), i.get("netmask"), i.get("gateway"))

def get_hostname():
    r = GET("/system/hostname")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    return (r.json().get("hostname") or "").encode("ascii", "ignore").decode("utf-8")

def set_hostname(hostname: str):
    r = PUT("/system/hostname", json={"hostname": hostname})
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    return (r.json().get("hostname") or "").encode("ascii", "ignore").decode("utf-8")

def set_mgmt_intf(device, ip_address, netmask, gateway):
    r = GET("/networks/ip-mgmt")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    ip_mgmt = r.json().get("ip_management", [])
    data = {}
    for i in ip_mgmt:
        if i.get("type") == "event":
            data["event"] = {
                "iface": i.get("iface"),
                "family": i.get("family"),
                "ip_address": i.get("ip_address"),
                "netmask": i.get("netmask"),
                "gateway": i.get("gateway"),
            }
            if gateway:
                data["event"]["gateway"] = None
    data["mgmt"] = {
        "iface": device,
        "family": "ipv4",
        "ip_address": ip_address,
        "netmask": netmask,
        "gateway": gateway,
    }
    PUT("/networks/ip-mgmt", json=data)

def set_event_intf(device, ip_address, netmask, gateway):
    r = GET("/networks/ip-mgmt")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    ip_mgmt = r.json().get("ip_management", [])
    data = {}
    for i in ip_mgmt:
        if i.get("type") == "mgmt":
            data["mgmt"] = {
                "iface": i.get("iface"),
                "family": i.get("family"),
                "ip_address": i.get("ip_address"),
                "netmask": i.get("netmask"),
                "gateway": i.get("gateway"),
            }
            if gateway:
                data["mgmt"]["gateway"] = None
    data["event"] = {
        "iface": device,
        "family": "ipv4",
        "ip_address": ip_address,
        "netmask": netmask,
        "gateway": gateway,
    }
    PUT("/networks/ip-mgmt", json=data)

def delete_event_intf():
    DELETE("/networks/ip-mgmt", params={"ip_type": "event"})

def delete_mgmt_intf():
    DELETE("/networks/ip-mgmt", params={"ip_type": "mgmt"})

# -----------------------------
# Services
# -----------------------------
def list_sources():
    r = GET("/service/")
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text}")
        sys.exit(1)
    services = r.json().get("results")
    if services:
        for s in services:
            if s.get("type") == "source":
                pprint.pprint(s)

def toggle_source(service: str, enable: bool):
    r = GET(f"/service/{service}")
    if r.status_code != 200:
        print(f"Error retrieving service -> {r.text}")
        sys.exit(1)
    data = r.json()
    try:
        if data.get("type") == "source":
            data["modules"].pop("sink", None)
        for _, s_values in data["modules"]["source"][0].items():
            source = {
                "source_type": data["module"],
                "address": s_values["address"],
                "port": s_values["port"],
            }
            break
        data["modules"]["source"] = [source]
    except Exception:
        sys.exit(1)
    data["enabled"] = bool(enable)
    r = PUT(f"/service/{service}", json=data)
    if r.status_code == 200:
        print(f"{service} source {'enabled' if enable else 'disabled'}")
    else:
        print(r.status_code)
        sys.exit(1)

# -----------------------------
# CLI wiring
# -----------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="EdgeStream Gateway CLI Util")
    subparsers = parser.add_subparsers(dest="command")

    # SYSTEM
    system_parser = subparsers.add_parser("system", help="System API Settings")
    system_parser.add_argument("--get-hostname", action="store_true", default=False)
    system_parser.add_argument("--set-hostname", action="store")
    system_parser.add_argument("--get-org-id", action="store_true", default=False)
    system_parser.add_argument("--set-org-id", action="store")
    system_parser.add_argument("--get-site-id", action="store_true", default=False)
    system_parser.add_argument("--set-site-id", action="store")
    system_parser.add_argument("--write-config", action="store_true", default=False)
    system_parser.add_argument("--display-config", action="store_true", default=False)
    system_parser.add_argument("--restore-config", action="store")
    system_parser.add_argument("--install-config", action="store")

    # NETWORK
    network_parser = subparsers.add_parser("network", help="Network API Settings")
    network_parser.add_argument("--get-network", action="store_true", default=False)
    network_parser.add_argument("--set-network", action="store_true", default=False)
    network_parser.add_argument("--get-dns", action="store_true", default=False)
    network_parser.add_argument("--set-dns", action="store")
    network_parser.add_argument("--delete-all-dns", action="store_true", default=False)
    network_parser.add_argument("--get-ntp", action="store_true", default=False)
    network_parser.add_argument("--set-ntp", action="store")
    network_parser.add_argument("--delete-all-ntp", action="store_true", default=False)
    network_parser.add_argument("--mgmt-device", action="store")
    network_parser.add_argument("--mgmt-ip-address", action="store")
    network_parser.add_argument("--mgmt-netmask", action="store")
    network_parser.add_argument("--mgmt-gateway", action="store")
    network_parser.add_argument("--event-device", action="store")
    network_parser.add_argument("--event-ip-address", action="store")
    network_parser.add_argument("--event-netmask", action="store")
    network_parser.add_argument("--event-gateway", action="store")
    network_parser.add_argument("--get-mgmt-intf", action="store_true", default=False)
    network_parser.add_argument("--set-mgmt-intf", action="store_true", default=False)
    network_parser.add_argument("--del-mgmt-intf", action="store_true", default=False)
    network_parser.add_argument("--get-event-intf", action="store_true", default=False)
    network_parser.add_argument("--set-event-intf", action="store_true", default=False)
    network_parser.add_argument("--del-event-intf", action="store_true", default=False)
    network_parser.add_argument("--get-interfaces", action="store_true", default=False)

    # SOURCES
    source_parser = subparsers.add_parser("source", help="Source Service API Settings")
    source_parser.add_argument("--list", action="store_true", default=False)
    source_parser.add_argument("--enable", action="store")
    source_parser.add_argument("--disable", action="store")

    # USERS
    user_parser = subparsers.add_parser("user", help="User API Settings")
    user_parser.add_argument("--passwd", nargs=2, action="store", help="Change password for <user> <new password>")
    user_parser.add_argument("--list-active-users", action="store_true", default=False)
    user_parser.add_argument("--list-pending-users", action="store_true", default=False)
    user_parser.add_argument("--enable-user", action="store")
    user_parser.add_argument("--disable-user", action="store")
    user_parser.add_argument("--generate-otp", action="store")
    user_parser.add_argument("--display-otp", action="store")
    user_parser.add_argument("--delete-otp", action="store")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        sys.exit(1)

    if args.command.lower() == "system":
        if args.get_hostname:
            print(get_hostname())
        elif args.set_hostname:
            print(set_hostname(args.set_hostname))
        elif args.get_org_id:
            print(get_org_id())
        elif args.set_org_id:
            print(set_org_id(args.set_org_id))
        elif args.get_site_id:
            print(get_site_id())
        elif args.set_site_id:
            print(set_site_id(args.set_site_id))
        elif args.write_config:
            write_config()
        elif args.display_config:
            display_config()
        elif args.restore_config:
            restore_config(args.restore_config)
        elif args.install_config:
            install_config(args.install_config)
        else:
            system_parser.print_help(sys.stderr)
            sys.exit(1)

    elif args.command.lower() == "source":
        if args.list:
            list_sources()
        elif args.enable:
            toggle_source(args.enable, True)
        elif args.disable:
            toggle_source(args.disable, False)
        else:
            source_parser.print_help(sys.stderr)
            sys.exit(1)

    elif args.command.lower() == "network":
        if args.get_network:
            get_network()
        elif args.set_network:
            print(vars(args))
        elif args.get_mgmt_intf:
            get_mgmt_intf()
        elif args.del_mgmt_intf:
            delete_mgmt_intf()
        elif args.get_event_intf:
            get_event_intf()
        elif args.get_interfaces:
            print(get_interface_list())
        elif args.set_mgmt_intf:
            if not args.mgmt_device or not args.mgmt_ip_address or not args.mgmt_netmask:
                print("Missing arguments to set mgmt intf")
            else:
                set_mgmt_intf(args.mgmt_device, args.mgmt_ip_address, args.mgmt_netmask, args.mgmt_gateway)
        elif args.set_event_intf:
            if not args.event_device or not args.event_ip_address or not args.event_netmask:
                print("Missing arguments to set event intf")
            else:
                set_event_intf(args.event_device, args.event_ip_address, args.event_netmask, args.event_gateway)
        elif args.del_event_intf:
            delete_event_intf()
        elif args.get_dns:
            print(get_dns())
        elif args.set_dns:
            set_dns(args.set_dns)
        elif args.delete_all_dns:
            del_dns()
        elif args.get_ntp:
            print(get_ntp())
        elif args.set_ntp:
            set_ntp(args.set_ntp)
        elif args.delete_all_ntp:
            del_ntp()
        else:
            network_parser.print_help(sys.stderr)
            sys.exit(1)

    elif args.command.lower() == "user":
        if args.passwd:
            cli_token = ENV_BEARER or os.environ.get("CLI_AUTH_TOKEN") or SECRETS.get("CLI_AUTH_TOKEN") or ""
            if not cli_token:
                print("Missing CLI token; set CLI_AUTH_TOKEN in secrets")
                sys.exit(1)
            change_passwd(args.passwd[0], args.passwd[1], cli_token)
        elif args.list_active_users:
            list_users()
        elif args.list_pending_users:
            list_pending_users()
        elif args.enable_user:
            enable_user(args.enable_user, True)
        elif args.disable_user:
            enable_user(args.disable_user, False)
        elif args.generate_otp:
            gen_otp(args.generate_otp)
        elif args.display_otp:
            display_otp(args.display_otp)
        elif args.delete_otp:
            delete_otp(args.delete_otp)
        else:
            user_parser.print_help(sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    try:
        check_privileges()
    except PermissionError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    main()
    sys.exit(0)

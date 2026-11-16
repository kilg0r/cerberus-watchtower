"""Live OS port visibility - what's listening, what's talking, and who owns it.

Uses psutil for the socket table and process metadata. Everything is computed
fresh per request; the dashboard polls. Process lookups that fail (system
processes, races with exiting pids) degrade gracefully instead of erroring.

macOS denies the system-wide socket table to unprivileged processes
(`psutil.net_connections` raises AccessDenied), so we fall back to parsing
`lsof -nP -i`, which works without sudo. That fallback sees the sockets of
processes owned by the current user - enough for a dev machine, but not the
whole box the way the Windows/Linux path is.
"""

import ipaddress
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import PurePath
from typing import NamedTuple

import psutil

# Ports we can name on sight - Cerberus stack first, then common services.
KNOWN_PORTS = {
    8765: "Cerberus Watchtower",
    5173: "Vite dev server",
    24282: "Serena dashboard",
    29979: "paper MCP server",
    1704: "Snapcast",
    8096: "Jellyfin",
    8123: "Home Assistant",
    80: "HTTP",
    443: "HTTPS",
    135: "Windows RPC",
    139: "NetBIOS",
    445: "SMB",
    3389: "Remote Desktop",
    5040: "Windows Connected Devices",
    5432: "PostgreSQL",
    1433: "SQL Server",
    6379: "Redis",
    27017: "MongoDB",
    3000: "dev server",
    8000: "dev server",
    8080: "dev server",
    11434: "Ollama",
    88: "Kerberos",
    631: "CUPS printing",
    5353: "mDNS / Bonjour",
    5900: "Screen Sharing (VNC)",
}

# Apple-specific labels, applied only on macOS: 5000 is Control Center there
# and a Kestrel dev server on the Windows box, so labelling it unconditionally
# would put the wrong name on a port that matters.
if sys.platform == "darwin":
    KNOWN_PORTS.update({
        548: "AFP",
        3283: "Apple Remote Desktop",
        5000: "macOS Control Center",
        7000: "AirPlay Receiver",
    })

_EXPOSED_ADDRS = {"0.0.0.0", "::"}


def _pretty_name(name: str, cmdline: list[str], hint: str | None) -> str:
    """Tools installed under a versioned path (~/.local/share/claude/versions/2.1.232
    - the `claude` CLI itself) leave psutil with the version string as the process
    name. argv0, or the OS socket table's command, is the truthful one."""
    if any(ch.isalpha() for ch in name):
        return name
    argv0 = PurePath(cmdline[0]).name if cmdline else ""
    return argv0 or hint or name


def _process_info(pid: int | None, cache: dict, name_hint: str | None = None) -> dict:
    if pid is None:
        return {"pid": None, "process": "unknown", "cmdline": None, "uptime_s": None, "rss_mb": None}
    if pid in cache:
        return cache[pid]
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            argv = proc.cmdline()
            cmdline = " ".join(argv)
            info = {
                "pid": pid,
                "process": _pretty_name(proc.name(), argv, name_hint),
                "cmdline": (cmdline[:160] + "…") if len(cmdline) > 160 else cmdline or None,
                "uptime_s": int(time.time() - proc.create_time()),
                "rss_mb": round(proc.memory_info().rss / (1024 * 1024), 1),
            }
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        info = {"pid": pid, "process": name_hint or f"system (pid {pid})", "cmdline": None,
                "uptime_s": None, "rss_mb": None}
    cache[pid] = info
    return info


class _Addr(NamedTuple):
    ip: str
    port: int


class _Conn(NamedTuple):
    """psutil.net_connections()-shaped record, so snapshot() is source-agnostic."""

    type: int  # 1 = SOCK_STREAM (tcp), 2 = SOCK_DGRAM (udp)
    status: str
    laddr: _Addr | None
    raddr: _Addr | None
    pid: int | None
    process: str | None = None  # command name from lsof, used when psutil is denied


def _parse_endpoint(text: str, ipv6: bool) -> _Addr | None:
    """'1.2.3.4:80' / '[::1]:80' / '*:*' -> _Addr, or None if unusable."""
    host, sep, port = text.rpartition(":")
    if not sep:
        return None
    host = host.strip("[]")
    if host == "*":
        host = "::" if ipv6 else "0.0.0.0"
    try:
        return _Addr(host, int(port))
    except ValueError:
        return _Addr(host, 0)  # '*' port - an unbound socket


def _lsof_connections() -> list[_Conn]:
    """Unprivileged socket table via lsof field output (macOS fallback)."""
    lsof = shutil.which("lsof")
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-i", "-F", "pcftPnT"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    conns: list[_Conn] = []
    pid: int | None = None
    command: str | None = None
    rec: dict = {}

    def flush() -> None:
        proto = rec.get("proto")
        name = rec.get("name")
        if not proto or not name:
            return
        ipv6 = rec.get("family") == "IPv6"
        local, _, remote = name.partition("->")
        laddr = _parse_endpoint(local, ipv6)
        if laddr is None or laddr.port == 0:
            return  # '*:*' - an open socket that isn't bound to anything yet
        conns.append(
            _Conn(
                type=1 if proto == "TCP" else 2,
                status=rec.get("status") or psutil.CONN_NONE,
                laddr=laddr,
                raddr=_parse_endpoint(remote, ipv6) if remote else None,
                pid=pid,
                process=command,
            )
        )

    for line in result.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            flush()
            rec = {}
            command = None
            pid = int(value) if value.isdigit() else None
        elif tag == "c":
            command = value
        elif tag == "f":
            flush()
            rec = {}
        elif tag == "t":
            rec["family"] = value
        elif tag == "P":
            rec["proto"] = value
        elif tag == "n":
            rec["name"] = value
        elif tag == "T" and value.startswith("ST="):
            rec["status"] = value[3:]
    flush()
    return conns


def _connections() -> list:
    """System socket table, however this OS is willing to hand it over."""
    try:
        return psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        return _lsof_connections()


def _is_private(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
        return parsed.is_private or parsed.is_loopback or parsed.is_link_local
    except ValueError:
        return True


def snapshot() -> dict:
    connections = _connections()
    process_cache: dict[int, dict] = {}

    listeners = []
    seen_listeners = set()
    for conn in connections:
        is_tcp_listen = conn.type == 1 and conn.status == psutil.CONN_LISTEN  # SOCK_STREAM
        is_udp_bound = conn.type == 2 and not conn.raddr  # SOCK_DGRAM
        if not (is_tcp_listen or is_udp_bound) or not conn.laddr:
            continue
        key = (conn.laddr.port, conn.laddr.ip, "tcp" if is_tcp_listen else "udp", conn.pid)
        if key in seen_listeners:
            continue
        seen_listeners.add(key)
        listeners.append(
            {
                "port": conn.laddr.port,
                "address": conn.laddr.ip,
                "proto": "tcp" if is_tcp_listen else "udp",
                "exposed": conn.laddr.ip in _EXPOSED_ADDRS,
                "label": KNOWN_PORTS.get(conn.laddr.port),
                **_process_info(conn.pid, process_cache, getattr(conn, "process", None)),
            }
        )
    listeners.sort(key=lambda l: (l["port"], l["proto"], l["address"]))

    # established connections grouped by (process, remote ip)
    groups: dict[tuple, dict] = {}
    for conn in connections:
        if conn.status != psutil.CONN_ESTABLISHED or not conn.raddr:
            continue
        info = _process_info(conn.pid, process_cache, getattr(conn, "process", None))
        key = (info["process"], conn.raddr.ip)
        entry = groups.setdefault(
            key,
            {
                "process": info["process"],
                "pid": info["pid"],
                "remote_ip": conn.raddr.ip,
                "external": not _is_private(conn.raddr.ip),
                "remote_ports": [],
                "count": 0,
            },
        )
        entry["count"] += 1
        if conn.raddr.port not in entry["remote_ports"] and len(entry["remote_ports"]) < 8:
            entry["remote_ports"].append(conn.raddr.port)
    talkers = sorted(groups.values(), key=lambda g: -g["count"])

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "listeners": listeners,
        "connections": talkers,
        "stats": {
            "tcp_listeners": sum(1 for l in listeners if l["proto"] == "tcp"),
            "udp_bound": sum(1 for l in listeners if l["proto"] == "udp"),
            "lan_exposed": sum(1 for l in listeners if l["exposed"] and l["proto"] == "tcp"),
            "established": sum(g["count"] for g in talkers),
            "external_hosts": len({g["remote_ip"] for g in talkers if g["external"]}),
            "processes": len({l["pid"] for l in listeners if l["pid"]}),
        },
    }

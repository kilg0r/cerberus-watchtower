"""Live OS port visibility - what's listening, what's talking, and who owns it.

Uses psutil for the socket table and process metadata. Everything is computed
fresh per request; the dashboard polls. Process lookups that fail (system
processes, races with exiting pids) degrade gracefully instead of erroring.
"""

import ipaddress
import time
from datetime import datetime, timezone

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
}

_EXPOSED_ADDRS = {"0.0.0.0", "::"}


def _process_info(pid: int | None, cache: dict) -> dict:
    if pid is None:
        return {"pid": None, "process": "unknown", "cmdline": None, "uptime_s": None, "rss_mb": None}
    if pid in cache:
        return cache[pid]
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            cmdline = " ".join(proc.cmdline())
            info = {
                "pid": pid,
                "process": proc.name(),
                "cmdline": (cmdline[:160] + "…") if len(cmdline) > 160 else cmdline or None,
                "uptime_s": int(time.time() - proc.create_time()),
                "rss_mb": round(proc.memory_info().rss / (1024 * 1024), 1),
            }
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        info = {"pid": pid, "process": f"system (pid {pid})", "cmdline": None,
                "uptime_s": None, "rss_mb": None}
    cache[pid] = info
    return info


def _is_private(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
        return parsed.is_private or parsed.is_loopback or parsed.is_link_local
    except ValueError:
        return True


def snapshot() -> dict:
    connections = psutil.net_connections(kind="inet")
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
                **_process_info(conn.pid, process_cache),
            }
        )
    listeners.sort(key=lambda l: (l["port"], l["proto"], l["address"]))

    # established connections grouped by (process, remote ip)
    groups: dict[tuple, dict] = {}
    for conn in connections:
        if conn.status != psutil.CONN_ESTABLISHED or not conn.raddr:
            continue
        info = _process_info(conn.pid, process_cache)
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

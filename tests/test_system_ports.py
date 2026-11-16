import subprocess

import psutil

from watchtower import system_ports
from watchtower.system_ports import _is_private, _lsof_connections, _pretty_name, snapshot

# Field output of `lsof -nP -i -F pcftPnT`, the macOS fallback source.
LSOF_SAMPLE = """\
p616
crapportd
f10
tIPv4
PTCP
n*:56141
TST=LISTEN
TQR=0
f21
tIPv6
PTCP
n[fe80:e::141c:82c9]:56141->[fe80:e::14e2:da40]:54892
TST=ESTABLISHED
f22
tIPv6
PUDP
n*:3722
p700
cidentityservicesd
f10
tIPv4
PUDP
n*:*
p800
cpostgres
f7
tIPv4
PTCP
n127.0.0.1:5432
TST=LISTEN
"""


def test_snapshot_shape_and_content():
    result = snapshot()
    assert set(result) == {"scanned_at", "listeners", "connections", "stats"}
    # every desktop OS has something listening (Windows RPC, macOS mDNS, ...)
    assert result["listeners"], "expected at least one listener"
    listener = result["listeners"][0]
    assert {"port", "address", "proto", "exposed", "label", "pid",
            "process", "cmdline", "uptime_s", "rss_mb"} <= set(listener)
    assert listener["proto"] in ("tcp", "udp")
    stats = result["stats"]
    assert stats["tcp_listeners"] >= 1
    assert stats["lan_exposed"] <= stats["tcp_listeners"]


def test_lsof_fallback_parsing(monkeypatch):
    monkeypatch.setattr(system_ports.shutil, "which", lambda _: "/usr/sbin/lsof")
    monkeypatch.setattr(
        system_ports.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, LSOF_SAMPLE, ""),
    )
    conns = _lsof_connections()

    # the unbound '*:*' socket is dropped; the other four survive
    assert len(conns) == 4
    listen_v4, established, udp, pg = conns
    assert (listen_v4.pid, listen_v4.process) == (616, "rapportd")
    assert listen_v4.laddr == ("0.0.0.0", 56141)  # '*' resolved by address family
    assert listen_v4.status == psutil.CONN_LISTEN and listen_v4.type == 1
    assert established.raddr == ("fe80:e::14e2:da40", 54892)
    assert udp.type == 2 and udp.laddr == ("::", 3722)
    assert (pg.pid, pg.laddr.port, pg.process) == (800, 5432, "postgres")


def test_lsof_fallback_survives_missing_binary(monkeypatch):
    monkeypatch.setattr(system_ports.shutil, "which", lambda _: None)
    assert _lsof_connections() == []


def test_pretty_name_prefers_argv0_over_versioned_exe():
    # ~/.local/share/claude/versions/2.1.232 leaves psutil naming the process
    # after the version directory entry
    assert _pretty_name("2.1.232", ["claude", "--resume"], "claude") == "claude"
    assert _pretty_name("2.1.232", [], "claude") == "claude"
    assert _pretty_name("postgres", ["/usr/local/bin/postgres"], None) == "postgres"


def test_is_private_classification():
    assert _is_private("127.0.0.1")
    assert _is_private("192.168.4.53")
    assert _is_private("10.0.0.1")
    assert not _is_private("160.79.104.10")
    assert _is_private("not-an-ip")  # unparseable defaults to non-external

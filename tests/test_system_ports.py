from watchtower.system_ports import _is_private, snapshot


def test_snapshot_shape_and_content():
    result = snapshot()
    assert set(result) == {"scanned_at", "listeners", "connections", "stats"}
    # a Windows box always has something listening (RPC on 135 at minimum)
    assert result["listeners"], "expected at least one listener"
    listener = result["listeners"][0]
    assert {"port", "address", "proto", "exposed", "label", "pid",
            "process", "cmdline", "uptime_s", "rss_mb"} <= set(listener)
    assert listener["proto"] in ("tcp", "udp")
    stats = result["stats"]
    assert stats["tcp_listeners"] >= 1
    assert stats["lan_exposed"] <= stats["tcp_listeners"]


def test_is_private_classification():
    assert _is_private("127.0.0.1")
    assert _is_private("192.168.4.53")
    assert _is_private("10.0.0.1")
    assert not _is_private("160.79.104.10")
    assert _is_private("not-an-ip")  # unparseable defaults to non-external

import pytest

from watchtower.ai import narrator
from watchtower.ai.summarizer import SummarizerError
from watchtower.drift import compute_drift, record_snapshot


def _arch(edges, packages, endpoints):
    return {
        "stack": "dotnet",
        "graph": {
            "nodes": [
                {
                    "id": "Demo.Api",
                    "folder": "presentation",
                    "is_test": False,
                    "framework": "net10.0",
                    "packages": packages,
                    "file_count": 5,
                },
                {
                    "id": "Demo.App",
                    "folder": "core",
                    "is_test": False,
                    "framework": "net10.0",
                    "packages": [],
                    "file_count": 9,
                },
            ],
            "edges": edges,
        },
        "handlers": [{"handler": "GetOrdersQueryHandler", "project": "Demo.App"}],
        "endpoints": endpoints,
        "stats": {"projects": 2, "endpoints": len(endpoints)},
    }


BASE = _arch(
    edges=[{"source": "Demo.Api", "target": "Demo.App", "kind": "project_ref"}],
    packages=[{"name": "MediatR", "version": "12.4.1"}],
    endpoints=[{"verb": "GET", "route": "/api/Orders", "project": "Demo.Api"}],
)
CHANGED = _arch(
    edges=[
        {"source": "Demo.Api", "target": "Demo.App", "kind": "project_ref"},
        {"source": "Demo.App", "target": "Demo.Api", "kind": "project_ref"},  # new edge
    ],
    packages=[{"name": "MediatR", "version": "13.0.0"}],  # version bump
    endpoints=[
        {"verb": "GET", "route": "/api/Orders", "project": "Demo.Api"},
        {"verb": "POST", "route": "/api/Orders", "project": "Demo.Api"},  # new endpoint
    ],
)


def test_record_snapshot_dedupes_identical_scans(session):
    assert record_snapshot(session, "demo", BASE) is True
    assert record_snapshot(session, "demo", BASE) is False  # same signature
    assert record_snapshot(session, "demo", CHANGED) is True


def test_compute_drift_single_snapshot_reports_no_change(session):
    record_snapshot(session, "demo", BASE)
    result = compute_drift(session, "demo")
    assert result["changed"] is False
    assert result["snapshot_count"] == 1
    assert result["changes"] is None


def test_compute_drift_detects_changes(session):
    record_snapshot(session, "demo", BASE)
    record_snapshot(session, "demo", CHANGED)
    result = compute_drift(session, "demo")

    assert result["changed"] is True
    assert result["snapshot_count"] == 2
    changes = result["changes"]
    assert changes["added_edges"] == ["Demo.App->Demo.Api"]
    assert changes["removed_edges"] == []
    assert changes["added_endpoints"] == ["POST /api/Orders"]
    assert changes["package_changes"] == [
        {"package": "MediatR", "from": "12.4.1", "to": "13.0.0", "project": "Demo.Api"}
    ]
    assert changes["stats_delta"]["endpoints"] == {"from": 1, "to": 2}


def test_narrative_cache_hit_miss(session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        narrator, "run_claude", lambda sig, prompt: calls.append(sig) or "Demo.App is the core."
    )
    signature = narrator.project_signature(BASE, "Demo.App")
    assert signature is not None
    assert "GetOrdersQueryHandler" in signature

    first = narrator.get_or_create_narrative(session, "demo", "Demo.App", signature)
    assert first["cached"] is False
    second = narrator.get_or_create_narrative(session, "demo", "Demo.App", signature)
    assert second["cached"] is True
    assert len(calls) == 1

    # changed structure -> new signature -> regenerates
    new_sig = narrator.project_signature(CHANGED, "Demo.App")
    third = narrator.get_or_create_narrative(session, "demo", "Demo.App", new_sig)
    assert third["cached"] is False
    assert len(calls) == 2


def test_project_signature_unknown_node():
    assert narrator.project_signature(BASE, "Nope") is None

import json
from pathlib import Path

import pytest

from watchtower.scanners.vue_scanner import analyze

ROUTER_JS = """
import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/orders/:id', name: 'order', component: () => import('../views/OrderView.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
"""

STORE_JS = """
import { api } from '../api/client'

export async function loadOrders() {
  const open = await api.get(`/Orders/Open/${restaurantId}`)
  const closed = await fetch('/api/orders/closed')
  const lookup = cache.get('not-a-url')
  return [open, closed]
}
"""


@pytest.fixture
def vue_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "vue-demo"
    (repo / "src" / "router").mkdir(parents=True)
    (repo / "src" / "views").mkdir()
    (repo / "src" / "stores").mkdir()
    (repo / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"vue": "^3.5.0", "pinia": "^2.0.0"},
                "devDependencies": {"vite": "^6.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (repo / "src" / "router" / "index.js").write_text(ROUTER_JS, encoding="utf-8")
    (repo / "src" / "views" / "HomeView.vue").write_text("<template/>", encoding="utf-8")
    (repo / "src" / "views" / "OrderView.vue").write_text("<template/>", encoding="utf-8")
    (repo / "src" / "stores" / "orders.js").write_text(STORE_JS, encoding="utf-8")
    return repo


def test_analyze_vue_repo(vue_repo: Path):
    result = analyze(vue_repo)
    assert result is not None
    assert result["stack"] == "vue"
    assert result["packages"]["dependencies"]["vue"] == "^3.5.0"

    routes = {r["path"]: r for r in result["routes"]}
    assert routes["/"]["name"] == "home"
    assert routes["/"]["component"] == "HomeView"
    assert routes["/orders/:id"]["component"] == "../views/OrderView.vue"

    urls = {c["url"] for c in result["api_calls"]}
    assert "/Orders/Open/${restaurantId}" in urls
    assert "/api/orders/closed" in urls
    assert "not-a-url" not in urls  # cache.get() screened out by URL prefix filter

    assert result["views"] == ["HomeView", "OrderView"]
    assert result["stores"] == ["orders"]


def test_analyze_returns_none_without_package_json(tmp_path: Path):
    assert analyze(tmp_path) is None

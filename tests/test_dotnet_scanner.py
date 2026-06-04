from pathlib import Path

import pytest

from watchtower.scanners.dotnet_scanner import analyze

SLN = """
Microsoft Visual Studio Solution File, Format Version 12.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Demo.Api", "Demo.Api\\Demo.Api.csproj", "{AAAA0000-0000-0000-0000-000000000001}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Demo.App", "Demo.App\\Demo.App.csproj", "{AAAA0000-0000-0000-0000-000000000002}"
EndProject
Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "presentation", "presentation", "{BBBB0000-0000-0000-0000-000000000001}"
EndProject
Global
	GlobalSection(NestedProjects) = preSolution
		{AAAA0000-0000-0000-0000-000000000001} = {BBBB0000-0000-0000-0000-000000000001}
	EndGlobalSection
EndGlobal
"""

API_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="MediatR" Version="12.4.1" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Demo.App\\Demo.App.csproj" />
  </ItemGroup>
</Project>
"""

APP_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>
</Project>
"""

HANDLER_CS = """using MediatR;
namespace Demo.App.Queries;

public class GetOrdersQuery : IRequest<List<OrderDto>> { }

public class GetOrdersQueryHandler
    : IRequestHandler<GetOrdersQuery, List<OrderDto>>
{
    private readonly IOrderService _orderService;

    public async Task<List<OrderDto>> Handle(GetOrdersQuery request, CancellationToken ct)
    {
        return await _orderService.GetOrders();
    }
}
"""

SERVICE_CS = """namespace Demo.App.Services;

public interface IOrderService
{
    Task<List<OrderDto>> GetOrders();
}

public class OrderService : IOrderService
{
    private readonly IPricingService _pricing;

    public async Task<List<OrderDto>> GetOrders()
    {
        var prices = await _pricing.GetPrices();
        prices.ToString();
        return Normalize(prices);
    }

    private List<OrderDto> Normalize(object prices)
    {
        return PriceHelper.Round(prices);
    }
}

public interface IPricingService
{
    Task<object> GetPrices();
}

public static class PriceHelper
{
    public static List<OrderDto> Round(object prices) => null;
}
"""

CONTROLLER_CS = """using MediatR;
namespace Demo.Api.Controllers;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("pending")]
    public async Task<IActionResult> GetPending()
    {
        var result = await _mediator.Send(new GetOrdersQuery());
        return Ok(result);
    }

    [HttpPost]
    public async Task<IActionResult> Create()
    {
        var result = await _mediator.Send(new CreateOrderCommand());
        return Ok(result);
    }
}
"""


@pytest.fixture
def dotnet_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    (repo / "Demo.Api" / "Controllers").mkdir(parents=True)
    (repo / "Demo.App" / "Queries").mkdir(parents=True)
    (repo / "demo.sln").write_text(SLN, encoding="utf-8")
    (repo / "Demo.Api" / "Demo.Api.csproj").write_text(API_CSPROJ, encoding="utf-8")
    (repo / "Demo.App" / "Demo.App.csproj").write_text(APP_CSPROJ, encoding="utf-8")
    (repo / "Demo.App" / "Queries" / "GetOrdersQuery.cs").write_text(
        HANDLER_CS, encoding="utf-8"
    )
    (repo / "Demo.App" / "Services").mkdir()
    (repo / "Demo.App" / "Services" / "OrderService.cs").write_text(
        SERVICE_CS, encoding="utf-8"
    )
    (repo / "Demo.Api" / "Controllers" / "OrdersController.cs").write_text(
        CONTROLLER_CS, encoding="utf-8"
    )
    return repo


def test_analyze_builds_project_graph(dotnet_repo: Path):
    result = analyze(dotnet_repo)
    assert result is not None
    assert result["stack"] == "dotnet"
    assert result["stats"]["projects"] == 2

    nodes = {n["id"]: n for n in result["graph"]["nodes"]}
    assert nodes["Demo.Api"]["folder"] == "presentation"
    assert nodes["Demo.Api"]["framework"] == "net10.0"
    assert nodes["Demo.Api"]["packages"] == [{"name": "MediatR", "version": "12.4.1"}]
    assert result["graph"]["edges"] == [
        {"source": "Demo.Api", "target": "Demo.App", "kind": "project_ref"}
    ]


def test_analyze_discovers_handlers_with_generic_response(dotnet_repo: Path):
    result = analyze(dotnet_repo)
    handlers = result["handlers"]
    assert len(handlers) == 1
    assert handlers[0]["handler"] == "GetOrdersQueryHandler"
    assert handlers[0]["request"] == "GetOrdersQuery"
    assert handlers[0]["response"] == "List<OrderDto>"
    assert handlers[0]["project"] == "Demo.App"


def test_analyze_maps_endpoints_to_handlers(dotnet_repo: Path):
    result = analyze(dotnet_repo)
    endpoints = {e["route"]: e for e in result["endpoints"]}

    pending = endpoints["/api/Orders/pending"]
    assert pending["verb"] == "GET"
    assert pending["request"] == "GetOrdersQuery"
    assert pending["handler"] == "GetOrdersQueryHandler"
    assert pending["handler_project"] == "Demo.App"

    create = endpoints["/api/Orders"]
    assert create["verb"] == "POST"
    assert create["request"] == "CreateOrderCommand"
    assert create["handler"] is None  # no handler exists for this one


def test_analyze_returns_none_without_solution(tmp_path: Path):
    assert analyze(tmp_path) is None


def test_class_methods_resolve_field_self_and_static_calls(dotnet_repo: Path):
    result = analyze(dotnet_repo)
    methods = result["class_methods"]

    get_orders = next(m for m in methods["OrderService"] if m["name"] == "GetOrders")
    assert {"type": "IPricingService", "method": "GetPrices", "via": "_pricing"} in get_orders["calls"]
    assert {"type": "OrderService", "method": "Normalize", "via": "this"} in get_orders["calls"]
    # BCL ceremony (ToString) is filtered out
    assert not any(c["method"] == "ToString" for c in get_orders["calls"])

    normalize = next(m for m in methods["OrderService"] if m["name"] == "Normalize")
    assert {"type": "PriceHelper", "method": "Round", "via": None} in normalize["calls"]

    # static helper's own method is indexed too
    assert [m["name"] for m in methods["PriceHelper"]] == ["Round"]


def test_handler_methods_traverse_to_services(dotnet_repo: Path):
    result = analyze(dotnet_repo)
    handle = next(
        m for m in result["class_methods"]["GetOrdersQueryHandler"] if m["name"] == "Handle"
    )
    assert handle["calls"] == [
        {"type": "IOrderService", "method": "GetOrders", "via": "_orderService"}
    ]
    assert result["stats"]["methods"] >= 4
    # interfaces are not method-indexed, implementations resolve the hop
    assert "IOrderService" not in result["class_methods"]
    assert result["implementations"]["IOrderService"] == ["OrderService"]

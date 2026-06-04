"""Static architecture analysis for .NET solutions.

Builds three things from a repo containing a .sln:
  1. Project dependency graph (.sln + .csproj ProjectReferences, NuGet packages,
     solution-folder grouping)
  2. MediatR handler index (request -> handler, via IRequestHandler<TReq, TResp>)
  3. Data flows: controller endpoints -> dispatched request -> handler -> project,
     plus all other dispatch sites (hubs, background services)

Pure text/XML parsing - no Roslyn, no build required. Heuristic by design; the
goal is an always-current eagle's-eye map, not a compiler-grade model.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {"bin", "obj", "node_modules", ".git", "publish", "TestResults"}

_SLN_PROJECT_RE = re.compile(
    r'Project\("\{[0-9A-F-]+\}"\)\s*=\s*"([^"]+)",\s*"([^"]+\.csproj)",\s*"\{([0-9A-F-]+)\}"',
    re.IGNORECASE,
)
_SLN_FOLDER_RE = re.compile(
    r'Project\("\{2150E333-8FDC-42A3-9474-1A3956D46DE8\}"\)\s*=\s*"([^"]+)",\s*"[^"]+",\s*"\{([0-9A-F-]+)\}"',
    re.IGNORECASE,
)
_SLN_NESTED_RE = re.compile(r"\{([0-9A-F-]+)\}\s*=\s*\{([0-9A-F-]+)\}", re.IGNORECASE)
_CLASS_RE = re.compile(r"\b(?:class|record)\s+(\w+)")
_HANDLER_RE = re.compile(r"IRequestHandler<")
_SEND_RE = re.compile(r"\bSend(?:Async)?\(\s*new\s+(\w+)")
_ROUTE_ATTR_RE = re.compile(r'\[Route\("([^"]*)"\)\]')
_HTTP_ATTR_RE = re.compile(r'\[Http(Get|Post|Put|Delete|Patch)(?:\("([^"]*)"\))?\]')
_CONTROLLER_CLASS_RE = re.compile(r"\bclass\s+(\w+Controller)\b")
_TYPE_DECL_RE = re.compile(r"\b(class|interface|record)\s+(\w+)")
_CTOR_MODIFIER_RE = re.compile(r"^(?:this|in|out|ref|params)\s+")
_ATTR_RE = re.compile(r"\[[^\]]*\]")
_FIELD_DECL_RE = re.compile(r"([\w?]+(?:<[^;={]*?>)?\??)\s+(_\w+)\s*(?:;|=>|=)")
_FIELD_USE_RE = re.compile(r"(_\w+)\s*\.")
_GET_SERVICE_RE = re.compile(r"Get(?:Required)?Service<\s*(\w+)")
_FIELD_TYPE_NOISE = {"var", "return", "await", "new", "string", "int", "bool", "object"}
_METHOD_DECL_RE = re.compile(
    r"^[ \t]*"
    r"(?:(?:public|private|protected|internal|static|async|virtual|override|sealed|partial|extern|unsafe|new)\s+)+"
    r"(?!class\b|record\b|interface\b|struct\b|enum\b|event\b|delegate\b|operator\b)"
    r"([\w?]+(?:<[^;{}=]*?>)?(?:\[\])?\??)\s+(\w+)\s*(?:<[^>\n]*>)?\s*\(",
    re.MULTILINE,
)
_FIELD_CALL_RE = re.compile(r"(_\w+)\.(\w+)\s*(?:<[^>\n]*>)?\s*\(")
_TYPE_CALL_RE = re.compile(r"(?<![\w.<])([A-Z]\w*)\.(\w+)\s*(?:<[^>\n]*>)?\s*\(")
_BARE_CALL_RE = re.compile(r"(?<![\w.<])([A-Z]\w*)\s*(?:<[^>\n]*>)?\s*\(")
_THIS_CALL_RE = re.compile(r"\bthis\.(\w+)\s*(?:<[^>\n]*>)?\s*\(")
# method names that are plumbing, not flow (BCL ceremony on any object)
_CALL_NOISE = {
    "ToString", "Equals", "GetHashCode", "GetType", "Dispose", "DisposeAsync",
    "ConfigureAwait", "ContinueWith", "CompareTo",
}


def _cs_files(root: Path):
    for path in root.rglob("*.cs"):
        if not SKIP_DIRS.intersection(path.relative_to(root).parts):
            yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _balanced_generic_args(text: str, start: int) -> str | None:
    """Given index just past 'IRequestHandler<', return the inside of the
    balanced angle brackets ('GetOrdersQuery, List<OrderDto>')."""
    depth, i = 1, start
    while i < len(text) and depth > 0:
        if text[i] == "<":
            depth += 1
        elif text[i] == ">":
            depth -= 1
        i += 1
    return text[start : i - 1] if depth == 0 else None


def _split_top_level(args: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in args:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return parts


def _balanced_parens(text: str, start: int) -> tuple[str, int] | None:
    """Given index just past '(', return (inside, index past ')')."""
    depth, i = 1, start
    while i < len(text) and depth > 0:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        i += 1
    return (text[start : i - 1], i) if depth == 0 else None


def _parse_params(params: str) -> list[dict]:
    """'IOrderService orders, ILogger<Foo> log = null' -> dependency type list."""
    deps = []
    for raw in _split_top_level(params):
        param = _ATTR_RE.sub("", raw).split("=")[0].strip()
        if not param:
            continue
        param = _CTOR_MODIFIER_RE.sub("", param)
        pieces = param.rsplit(None, 1)
        if len(pieces) != 2:
            continue
        type_str = pieces[0].strip()
        base = re.match(r"(\w+)", type_str)
        if base:
            deps.append({"type": type_str, "name": base.group(1)})
    return deps


def _extract_methods(class_name: str, body: str, body_offset: int, content: str) -> list[dict]:
    """Heuristic method index for one class body: declared methods plus the raw
    call sites inside each method (field calls, Type.Static calls, self calls,
    MediatR sends). Resolution against the global type index happens later."""
    decls = list(_METHOD_DECL_RE.finditer(body))
    own_names = {d.group(2) for d in decls} - {class_name}
    methods = []
    for index, decl in enumerate(decls):
        segment_end = decls[index + 1].start() if index + 1 < len(decls) else len(body)
        segment = body[decl.end() : segment_end]
        field_calls = dict.fromkeys(
            (field, meth)
            for field, meth in _FIELD_CALL_RE.findall(segment)
            if meth not in _CALL_NOISE
        )
        type_calls = dict.fromkeys(
            (type_name, meth)
            for type_name, meth in _TYPE_CALL_RE.findall(segment)
            if meth not in _CALL_NOISE
        )
        self_calls = dict.fromkeys(
            name
            for name in _BARE_CALL_RE.findall(segment) + _THIS_CALL_RE.findall(segment)
            if name in own_names
        )
        methods.append(
            {
                "name": decl.group(2),
                "line": content.count("\n", 0, body_offset + decl.start()) + 1,
                "field_calls": list(field_calls),
                "type_calls": list(type_calls),
                "self_calls": list(self_calls),
                "sends": list(dict.fromkeys(_SEND_RE.findall(segment))),
            }
        )
    return methods


def _extract_types(
    project_name: str,
    project_dir: Path,
    types: dict,
    class_deps: dict,
    base_lists: dict,
    class_fields: dict,
    class_uses: dict,
    class_methods_raw: dict,
) -> None:
    """Index every class/interface/record: declaring project, constructor-injected
    dependencies (classic + C# primary constructors), base/interface list, field
    declarations (_name -> type), field/GetService usages, and per-method call
    sites in the body."""
    for cs in _cs_files(project_dir):
        content = _read(cs)
        decls = list(_TYPE_DECL_RE.finditer(content))
        for index, decl in enumerate(decls):
            kind, name = decl.group(1), decl.group(2)
            body_end = decls[index + 1].start() if index + 1 < len(decls) else len(content)
            body = content[decl.start() : body_end]
            if name in types:
                continue  # first declaration wins (partials, duplicates)
            types[name] = {
                "kind": kind,
                "project": project_name,
                "file": str(cs.relative_to(project_dir.parent)),
            }

            brace = content.find("{", decl.end())
            semi = content.find(";", decl.end())
            end = min(x for x in (brace, semi) if x != -1) if max(brace, semi) != -1 else len(content)
            header = content[decl.end() : end]

            deps = []
            # primary constructor: class Foo(IBar bar) : IThing
            stripped = header.lstrip()
            offset = len(header) - len(stripped)
            if stripped.startswith("<"):  # skip generic type params
                close = stripped.find(">")
                stripped = stripped[close + 1 :].lstrip() if close != -1 else stripped
                offset = len(header) - len(stripped)
            if stripped.startswith("("):
                result = _balanced_parens(header, offset + 1)
                if result:
                    deps.extend(_parse_params(result[0]))

            # base/interface list: after top-level ':' in the header
            colon = header.find(":")
            if colon != -1:
                bases = []
                for base in _split_top_level(header[colon + 1 :]):
                    match = re.match(r"(\w+)", base.strip())
                    if match:
                        bases.append(match.group(1))
                if bases:
                    base_lists[name] = bases

            # classic constructor(s) inside the body
            if kind != "interface":
                for ctor in re.finditer(
                    rf"(?:public|internal|protected)\s+{re.escape(name)}\s*\(", body
                ):
                    result = _balanced_parens(body, ctor.end())
                    if result:
                        deps.extend(_parse_params(result[0]))

            if deps:
                seen = set()
                class_deps[name] = [
                    d for d in deps if not (d["name"] in seen or seen.add(d["name"]))
                ]

            if kind != "interface":
                # field declarations: protected IOrderService _orderService; / => / =
                fields = {}
                for field_match in _FIELD_DECL_RE.finditer(body):
                    type_str, field = field_match.group(1), field_match.group(2)
                    base = re.match(r"(\w+)", type_str)
                    if base and base.group(1) not in _FIELD_TYPE_NOISE:
                        fields.setdefault(field, base.group(1))
                if fields:
                    class_fields[name] = fields
                # usages: _field.Method() plus GetService<IFoo>() resolutions
                uses = list(dict.fromkeys(_FIELD_USE_RE.findall(body)))
                services = list(dict.fromkeys(_GET_SERVICE_RE.findall(body)))
                if uses or services:
                    class_uses[name] = {"fields": uses, "services": services}
                # per-method call sites (resolved later in analyze)
                methods = _extract_methods(name, body, decl.start(), content)
                if methods:
                    class_methods_raw[name] = methods


def parse_solution(repo_path: Path) -> dict | None:
    sln = next(iter(repo_path.glob("*.sln")), None)
    if sln is None:
        return None
    content = _read(sln)
    folders = {guid.upper(): name for name, guid in _SLN_FOLDER_RE.findall(content)}
    nesting = {}
    nested_section = re.search(
        r"GlobalSection\(NestedProjects\)(.*?)EndGlobalSection", content, re.DOTALL
    )
    if nested_section:
        nesting = {
            child.upper(): parent.upper()
            for child, parent in _SLN_NESTED_RE.findall(nested_section.group(1))
        }
    projects = []
    for name, rel_path, guid in _SLN_PROJECT_RE.findall(content):
        folder = folders.get(nesting.get(guid.upper(), ""), None)
        projects.append({"name": name, "rel_path": rel_path, "folder": folder})
    return {"sln": sln.name, "projects": projects}


def parse_csproj(csproj_path: Path) -> dict:
    info = {"framework": None, "packages": [], "project_refs": []}
    try:
        root = ET.fromstring(_read(csproj_path))
    except ET.ParseError:
        return info
    for el in root.iter():
        if el.tag == "TargetFramework" and el.text:
            info["framework"] = el.text.strip()
        elif el.tag == "PackageReference":
            name = el.attrib.get("Include")
            if name:
                info["packages"].append(
                    {"name": name, "version": el.attrib.get("Version", "")}
                )
        elif el.tag == "ProjectReference":
            include = el.attrib.get("Include", "")
            if include:
                info["project_refs"].append(Path(include.replace("\\", "/")).stem)
    return info


def discover_handlers(project_name: str, project_dir: Path) -> list[dict]:
    handlers = []
    for cs in _cs_files(project_dir):
        content = _read(cs)
        if "IRequestHandler<" not in content:
            continue
        for match in _HANDLER_RE.finditer(content):
            args = _balanced_generic_args(content, match.end())
            if not args:
                continue
            parts = _split_top_level(args)
            request = parts[0].strip("? ")
            response = parts[1] if len(parts) > 1 else None
            classes = [m for m in _CLASS_RE.finditer(content) if m.start() < match.start()]
            handler = classes[-1].group(1) if classes else cs.stem
            handlers.append(
                {
                    "handler": handler,
                    "request": request,
                    "response": response,
                    "project": project_name,
                    "file": str(cs.relative_to(project_dir.parent)),
                }
            )
    return handlers


def _controller_route(content: str, controller: str) -> str:
    match = _ROUTE_ATTR_RE.search(content)
    template = match.group(1) if match else ""
    return template.replace("[controller]", controller.removesuffix("Controller"))


def discover_endpoints(project_name: str, project_dir: Path) -> list[dict]:
    endpoints = []
    for cs in _cs_files(project_dir):
        if not cs.name.endswith("Controller.cs"):
            continue
        content = _read(cs)
        class_match = _CONTROLLER_CLASS_RE.search(content)
        if not class_match:
            continue
        controller = class_match.group(1)
        base_route = _controller_route(content, controller)
        http_attrs = list(_HTTP_ATTR_RE.finditer(content))
        for i, attr in enumerate(http_attrs):
            section_end = http_attrs[i + 1].start() if i + 1 < len(http_attrs) else len(content)
            section = content[attr.end() : section_end]
            send = _SEND_RE.search(section)
            sub_route = attr.group(2) or ""
            route = "/".join(p for p in [base_route.strip("/"), sub_route.strip("/")] if p)
            endpoints.append(
                {
                    "verb": attr.group(1).upper(),
                    "route": f"/{route}",
                    "controller": controller,
                    "request": send.group(1) if send else None,
                    "project": project_name,
                    "file": str(cs.relative_to(project_dir.parent)),
                }
            )
    return endpoints


def discover_dispatch_sites(project_name: str, project_dir: Path) -> list[dict]:
    """Every `Send(new X...)` site - covers hubs and background services too."""
    sites = []
    for cs in _cs_files(project_dir):
        content = _read(cs)
        for match in _SEND_RE.finditer(content):
            classes = [m for m in _CLASS_RE.finditer(content) if m.start() < match.start()]
            sites.append(
                {
                    "request": match.group(1),
                    "context": classes[-1].group(1) if classes else cs.stem,
                    "project": project_name,
                    "file": str(cs.relative_to(project_dir.parent)),
                }
            )
    return sites


def analyze(repo_path: Path) -> dict | None:
    """Full architecture snapshot for a .NET solution repo."""
    solution = parse_solution(repo_path)
    if solution is None:
        return None

    nodes, edges, handlers, endpoints, dispatch_sites = [], [], [], [], []
    types, class_deps, base_lists, class_fields, class_uses = {}, {}, {}, {}, {}
    class_methods_raw: dict[str, list[dict]] = {}
    known = {p["name"] for p in solution["projects"]}

    for project in solution["projects"]:
        csproj = repo_path / project["rel_path"].replace("\\", "/")
        project_dir = csproj.parent
        info = parse_csproj(csproj)
        is_test = ".Tests" in project["name"] or project["folder"] == "tests"

        if project_dir.is_dir():
            project_handlers = discover_handlers(project["name"], project_dir)
            handlers.extend(project_handlers)
            endpoints.extend(discover_endpoints(project["name"], project_dir))
            dispatch_sites.extend(discover_dispatch_sites(project["name"], project_dir))
            if not is_test:  # type/dependency index skips test projects
                _extract_types(
                    project["name"], project_dir, types, class_deps,
                    base_lists, class_fields, class_uses, class_methods_raw,
                )
            file_count = sum(1 for _ in _cs_files(project_dir))
        else:
            project_handlers, file_count = [], 0

        nodes.append(
            {
                "id": project["name"],
                "label": project["name"],
                "kind": "project",
                "folder": project["folder"],
                "is_test": is_test,
                "framework": info["framework"],
                "packages": info["packages"],
                "file_count": file_count,
                "handler_count": len(project_handlers),
            }
        )
        edges.extend(
            {"source": project["name"], "target": ref, "kind": "project_ref"}
            for ref in info["project_refs"]
            if ref in known
        )

    handler_by_request = {h["request"]: h for h in handlers}
    for endpoint in endpoints:
        handler = handler_by_request.get(endpoint["request"]) if endpoint["request"] else None
        endpoint["handler"] = handler["handler"] if handler else None
        endpoint["handler_project"] = handler["project"] if handler else None

    # interface -> implementing classes (resolved against the global type index)
    implementations: dict[str, list[str]] = {}
    for class_name, bases in base_lists.items():
        if types.get(class_name, {}).get("kind") == "interface":
            continue
        for base in bases:
            if types.get(base, {}).get("kind") == "interface":
                implementations.setdefault(base, []).append(class_name)

    # class -> MediatR requests it dispatches (nested sends for flow drilling)
    class_sends: dict[str, list[str]] = {}
    for site in dispatch_sites:
        sends = class_sends.setdefault(site["context"], [])
        if site["request"] not in sends:
            sends.append(site["request"])

    # class -> services it actually uses: field usages resolved through the
    # inheritance chain (ServiceInjection-style base classes) + GetService<T>()
    def _inherited_fields(class_name: str) -> dict:
        merged, current, hops = {}, class_name, 0
        while current and hops < 6:  # cycle/depth guard
            merged = {**class_fields.get(current, {}), **merged}  # own fields win
            current = next(
                (b for b in base_lists.get(current, [])
                 if types.get(b, {}).get("kind") == "class"),
                None,
            )
            hops += 1
        return merged

    class_service_uses: dict[str, list[dict]] = {}
    for class_name, uses in class_uses.items():
        fields = _inherited_fields(class_name)
        resolved = []
        for field in uses["fields"]:
            type_name = fields.get(field)
            if type_name and type_name in types:
                resolved.append({"field": field, "type": type_name})
        for service in uses["services"]:
            if service in types:
                resolved.append({"field": None, "type": service})
        if resolved:
            seen = set()
            class_service_uses[class_name] = [
                r for r in resolved if not (r["type"] in seen or seen.add(r["type"]))
            ]

    # class -> declared methods with calls resolved against the type index:
    # field calls through the inheritance chain, Type.Static calls, self calls.
    # This is the method-level call graph that powers full-codebase traversal.
    class_methods: dict[str, list[dict]] = {}
    for class_name, methods in class_methods_raw.items():
        fields = _inherited_fields(class_name)
        resolved_methods = []
        for method in methods:
            calls = []
            for field, meth in method["field_calls"]:
                type_name = fields.get(field)
                if type_name and type_name in types:
                    calls.append({"type": type_name, "method": meth, "via": field})
            for type_name, meth in method["type_calls"]:
                if type_name in types and type_name != class_name:
                    calls.append({"type": type_name, "method": meth, "via": None})
            for meth in method["self_calls"]:
                calls.append({"type": class_name, "method": meth, "via": "this"})
            seen = set()
            calls = [
                c for c in calls
                if not ((c["type"], c["method"]) in seen or seen.add((c["type"], c["method"])))
            ]
            resolved_methods.append(
                {
                    "name": method["name"],
                    "line": method["line"],
                    "calls": calls,
                    "sends": method["sends"],
                }
            )
        class_methods[class_name] = resolved_methods

    return {
        "stack": "dotnet",
        "solution": solution["sln"],
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "graph": {"nodes": nodes, "edges": edges},
        "handlers": handlers,
        "endpoints": endpoints,
        "dispatch_sites": dispatch_sites,
        "types": types,
        "class_deps": class_deps,
        "implementations": implementations,
        "class_sends": class_sends,
        "class_service_uses": class_service_uses,
        "class_methods": class_methods,
        "stats": {
            "projects": len(nodes),
            "test_projects": sum(1 for n in nodes if n["is_test"]),
            "project_refs": len(edges),
            "handlers": len(handlers),
            "endpoints": len(endpoints),
            "methods": sum(len(m) for m in class_methods.values()),
            "packages": len({p["name"] for n in nodes for p in n["packages"]}),
        },
    }

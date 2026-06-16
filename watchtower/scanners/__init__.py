"""Architecture scanner dispatch, shared by the live API and the git-history
views (which scan a repo's tree as it stood at a past commit).

Stack-specific scanners (dotnet / vue / python) build real dependency and
call graphs; every other stack falls back to the generic inventory scanner, so
nothing ever returns nothing-at-all for an existing tree.
"""

from ..config import RepoConfig
from . import dotnet_scanner, generic_scanner, python_scanner, vue_scanner

_ARCHITECTURE_SCANNERS = {
    "dotnet": dotnet_scanner.analyze,
    "vue": vue_scanner.analyze,
    "python": python_scanner.analyze,
}


def scan_architecture(repo: RepoConfig) -> dict | None:
    """Run the stack-specific architecture scanner, falling back to the generic
    inventory scanner. Returns None only when nothing analyzable was found."""
    scanner = _ARCHITECTURE_SCANNERS.get(repo.stack)
    result = scanner(repo.path) if scanner else None
    if result is None:  # no dedicated scanner, or it found nothing analyzable
        result = generic_scanner.analyze(repo.path, stack=repo.stack)
    return result

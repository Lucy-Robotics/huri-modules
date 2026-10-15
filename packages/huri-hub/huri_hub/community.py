"""The community: GitHub repositories tagged `huri-module`.

Read without installing anything: their pyproject.toml (the `huri.modules`
entry points) and each module's huri_module.toml (summary, supported huri-core
range). Nothing here is reviewed; `huri hub install` installs one only when
asked explicitly.
"""

import json
import urllib.parse
from typing import Any, Dict, List, Optional

from huri_hub.registry import HubModule
from huri_hub.web import GITHUB_API, RAW, Fetch, http_fetch

from huri_core.core.manifest import ManifestError, loads_manifest, loads_toml

TOPIC = "huri-module"


def search(query: str = "", fetch: Fetch = http_fetch) -> List[Dict[str, Any]]:
    """Repositories tagged `huri-module` matching `query` (only listed)."""
    q = urllib.parse.quote(f"topic:{TOPIC} {query}".strip())
    text = fetch(f"{GITHUB_API}/search/repositories?q={q}&per_page=50")
    if text is None:
        return []
    return [
        {
            "repo": item["full_name"],
            "description": item.get("description") or "",
            "url": item["html_url"],
            "ref": item.get("default_branch", "main"),
        }
        for item in json.loads(text).get("items", [])
    ]


def read_repository(repo: Dict[str, Any], fetch: Fetch = http_fetch) -> List[HubModule]:
    """The modules a repository provides, from its pyproject.toml and manifests."""

    def raw(path: str) -> Optional[str]:
        return fetch(RAW.format(repo=repo["repo"], ref=repo["ref"], path=path))

    pyproject = raw("pyproject.toml")
    if pyproject is None:
        return []
    try:
        project = loads_toml(pyproject).get("project", {})
    except ValueError:  # malformed TOML
        return []
    entry_points = project.get("entry-points", {}).get("huri.modules", {})

    modules = []
    for name, target in entry_points.items():
        package = target.split(":", 1)[0].split(".", 1)[0]
        manifest = None
        for path in (
            f"{package}/huri_module.toml",
            f"{package}/manifests/{name}/huri_module.toml",
            "huri_module.toml",
        ):
            text = raw(path)
            if text is None:
                continue
            try:
                candidate = loads_manifest(text, f"{repo['repo']}/{path}")
            except ManifestError:
                continue
            if candidate.name == name:
                manifest = candidate
                break
        modules.append(
            HubModule(
                name=name,
                summary=manifest.summary if manifest else repo.get("description", ""),
                huri_core=manifest.huri_core if manifest else None,
                source=repo["url"],
                install=f"git+{repo['url']}.git",
                verified=False,
            )
        )
    return modules


def catalog(registry: List[HubModule], community: List[HubModule]) -> List[HubModule]:
    """Both sources by name; a verified entry wins over a community one."""
    merged: Dict[str, HubModule] = {m.name: m for m in community}
    merged.update({m.name: m for m in registry})
    return sorted(merged.values(), key=lambda m: m.name)

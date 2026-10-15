"""What `huri hub` reads over the network, and where its notes go.

Every reader takes a `Fetch`, so the tests fake every URL.
"""

import os
import shutil
import sys
import urllib.error
import urllib.request
from typing import Callable, Dict, Mapping, Optional

GITHUB_API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"

# url -> text, or None when the resource does not exist (HTTP 404).
Fetch = Callable[[str], Optional[str]]
# Where notes and warnings go (stderr), apart from the command's output.
Warn = Callable[[str], None]


class HubError(RuntimeError):
    pass


def git_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """`environ` (default: this process's) for a git process, pip's git+https
    installs included, that must never wait for credentials: no terminal
    prompt, an askpass that answers nothing, no desktop askpass (SSH_ASKPASS)
    and no interactive credential manager. A repository that is missing or
    private (HTTP 401) then fails at once instead of asking for a login."""
    env = dict(os.environ if environ is None else environ)
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_ASKPASS=shutil.which("true") or "echo",
        GCM_INTERACTIVE="never",
    )
    env.pop("SSH_ASKPASS", None)
    return env


def warn_stderr(message: str) -> None:
    print(f"huri hub: {message}", file=sys.stderr)


def http_fetch(url: str) -> Optional[str]:
    headers = {"User-Agent": "huri-hub", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(GITHUB_API):
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            body: bytes = response.read()
            return body.decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise HubError(f"{url}: HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise HubError(f"{url}: {e.reason}") from e

#!/usr/bin/env python3
"""Homepage service-widget helpers: docs catalog, secrets, URL rewrite."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

DOCS_RAW_BASE = (
    "https://raw.githubusercontent.com/gethomepage/homepage/dev/docs/widgets/services"
)
DOCS_TREE_API = (
    "https://api.github.com/repos/gethomepage/homepage/git/trees/dev?recursive=1"
)
WIDGET_HOST = os.environ.get("HOMEPAGE_WIDGET_HOST", "host.docker.internal")

# Seed catalog from https://gethomepage.dev/widgets/services/ (required secret fields).
# Synced/expanded when refresh_widget_docs() succeeds.
SEED_CATALOG: dict[str, dict] = {
    "portainer": {
        "match": ["portainer"],
        "required": ["key", "env"],
        "optional": ["fields", "kubernetes"],
        "default_scheme": "https",
        "default_fields": ["running", "stopped", "total"],
        "docs": f"{DOCS_RAW_BASE}/portainer.md",
    },
    "jellyfin": {
        "match": ["jellyfin"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/jellyfin.md",
    },
    "emby": {
        "match": ["emby"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/emby.md",
    },
    "plex": {
        "match": ["plex"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/plex.md",
    },
    "sonarr": {
        "match": ["sonarr"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/sonarr.md",
    },
    "radarr": {
        "match": ["radarr"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/radarr.md",
    },
    "nzbget": {
        "match": ["nzbget"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/nzbget.md",
    },
    "qbittorrent": {
        "match": ["qbittorrent", "qbit"],
        "required": ["username", "password"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/qbittorrent.md",
    },
    "transmission": {
        "match": ["transmission"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/transmission.md",
    },
    "uptime-kuma": {
        "match": ["uptime-kuma", "uptimekuma"],
        "required": ["slug"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/uptime-kuma.md",
    },
    "watchtower": {
        "match": ["watchtower"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/watchtower.md",
    },
    "traefik": {
        "match": ["traefik"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/traefik.md",
    },
    "grafana": {
        "match": ["grafana"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/grafana.md",
    },
    "prometheus": {
        "match": ["prometheus"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/prometheus.md",
    },
    "nextcloud": {
        "match": ["nextcloud"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/nextcloud.md",
    },
    "homeassistant": {
        "match": ["homeassistant", "home-assistant"],
        "required": ["key"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/homeassistant.md",
    },
    "filebrowser": {
        "match": ["filebrowser"],
        "required": ["username", "password"],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/filebrowser.md",
    },
    "whatsupdocker": {
        "match": ["whatsupdocker", "wud"],
        "required": [],
        "default_scheme": "http",
        "docs": f"{DOCS_RAW_BASE}/whatsupdocker.md",
    },
}


def cache_path(config_dir: Path) -> Path:
    return config_dir / ".cache" / "homepage-widget-catalog.json"


def load_catalog(config_dir: Path) -> dict[str, dict]:
    path = cache_path(config_dir)
    catalog = dict(SEED_CATALOG)
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            widgets = cached.get("widgets", cached)
            if isinstance(widgets, dict):
                for slug, meta in widgets.items():
                    if isinstance(meta, dict):
                        catalog[slug] = {**catalog.get(slug, {}), **meta}
        except (json.JSONDecodeError, OSError):
            pass
    return catalog


def _http_get(url: str, timeout: int = 20) -> bytes:
    """GET with certifi/system SSL, falling back to curl on macOS cert issues."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "homepage-docker-sync", "Accept": "*/*"},
    )
    try:
        import ssl

        ctx = ssl.create_default_context()
        try:
            import certifi  # type: ignore

            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except Exception:
        # curl uses macOS keychain certs and is more reliable here
        import subprocess

        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", str(timeout), url],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise urllib.error.URLError(
                result.stderr.decode("utf-8", errors="replace") or "curl failed"
            )
        return result.stdout


def refresh_widget_docs(config_dir: Path, timeout: int = 20) -> tuple[dict[str, dict], str]:
    """Pull widget slug list from Homepage docs on GitHub; merge into local cache."""
    catalog = load_catalog(config_dir)
    try:
        payload = json.loads(_http_get(DOCS_TREE_API, timeout=timeout).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return catalog, f"docs refresh failed: {exc}"

    tree = payload.get("tree") or []
    slugs: list[str] = []
    for entry in tree:
        path = entry.get("path", "")
        if not path.startswith("docs/widgets/services/") or not path.endswith(".md"):
            continue
        name = path.rsplit("/", 1)[-1]
        if name in ("index.md",):
            continue
        slug = name[:-3]
        slugs.append(slug)
        if slug not in catalog:
            catalog[slug] = {
                "match": [slug.replace("-", ""), slug],
                "required": [],
                "default_scheme": "http",
                "docs": f"{DOCS_RAW_BASE}/{slug}.md",
                "from_docs": True,
            }

    for slug in ("portainer",):
        if slug in catalog:
            enriched = _parse_widget_doc(slug, timeout=timeout)
            if enriched:
                catalog[slug] = {**catalog[slug], **enriched}

    path = cache_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"source": DOCS_TREE_API, "count": len(catalog), "widgets": catalog},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return catalog, f"docs refresh ok: {len(slugs)} widget pages, {len(catalog)} catalog entries"


def _parse_widget_doc(slug: str, timeout: int = 15) -> dict | None:
    url = f"{DOCS_RAW_BASE}/{slug}.md"
    try:
        text = _http_get(url, timeout=timeout).decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    match = re.search(r"```yaml\s*\n(.*?)```", text, re.S | re.I)
    if not match:
        return None
    block = match.group(1)
    required: list[str] = []
    for key in ("key", "env", "username", "password", "slug", "token"):
        if re.search(rf"^\s*{key}:", block, re.M):
            required.append(key)
    meta: dict = {"docs": url, "required": required}
    if "fields:" in block and slug == "portainer":
        meta["default_fields"] = ["running", "stopped", "total"]
    if re.search(r"url:\s*https://", block):
        meta["default_scheme"] = "https"
    return meta


def load_widget_secrets(config_dir: Path) -> dict[str, dict]:
    """
    Load secrets from widget-secrets.yaml (gitignored).

    Format:
      portainer:          # by widget type and/or container name
        key: ptr_...
        env: 3
      containers:
        portainer:
          key: ptr_...
          env: 3
    """
    path = config_dir / "widget-secrets.yaml"
    if not path.is_file():
        return {}
    return _parse_simple_secrets(path.read_text(encoding="utf-8"))


def _parse_simple_secrets(text: str) -> dict[str, dict]:
    """Minimal YAML subset parser for flat/nested secret maps (no PyYAML)."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s*)([^:#]+):\s*(.*?)\s*$", raw)
        if not match:
            continue
        indent_s, key, value = match.groups()
        indent = len(indent_s)
        key = key.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _coerce(value)
    return root


def _coerce(value: str):
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def match_widget_type(image: str, name: str, catalog: dict[str, dict]) -> str | None:
    image_l = image.lower()
    name_l = name.lower()
    short = image_l.split("/")[-1].split(":")[0]
    candidates: list[tuple[int, str]] = []

    for slug, meta in catalog.items():
        needles = [str(x).lower() for x in (meta.get("match") or [slug]) if x]
        # Always consider the official slug itself
        if slug.lower() not in needles:
            needles.append(slug.lower())
        for n in needles:
            score = len(n)
            strong = (
                short == n
                or short.startswith(n + "-")
                or f"/{n}:" in f"/{image_l}"
                or f"/{n}/" in f"/{image_l}"
                or name_l == n
                or name_l.startswith(n + "-")
            )
            if strong:
                candidates.append((score + 100, slug))
                break
            # Seed entries may match image substring; docs-only slugs must be strong.
            if not meta.get("from_docs") and n in image_l:
                candidates.append((score, slug))
                break

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def secrets_for(widget_type: str, container_name: str, secrets: dict) -> dict:
    out: dict = {}
    if isinstance(secrets.get(widget_type), dict):
        out.update(secrets[widget_type])
    containers = secrets.get("containers")
    if isinstance(containers, dict) and isinstance(containers.get(container_name), dict):
        out.update(containers[container_name])
    # Also allow top-level container name key
    if isinstance(secrets.get(container_name), dict):
        out.update(secrets[container_name])
    return out


def widget_base_url(scheme: str, host_port: str | None) -> str | None:
    if not host_port:
        return None
    return f"{scheme}://{WIDGET_HOST}:{host_port}"


def rewrite_widget_url(url: str | None, host_port: str | None, scheme: str | None = None) -> str | None:
    """Rewrite widget URLs so Homepage container can reach host-published ports."""
    if not url and host_port:
        return widget_base_url(scheme or "http", host_port)
    if not url:
        return None
    match = re.match(r"^(https?)://([^/:]+)(?::(\d+))?(.*)$", url)
    if not match:
        return url
    url_scheme, host, port, rest = match.groups()
    use_scheme = scheme or url_scheme
    # Prefer explicitly detected service port (from href) over a stale URL port.
    use_port = host_port or port
    host_l = host.lower()
    rewrite_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
    is_private = bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)", host))
    if host_l in rewrite_hosts or is_private:
        if not use_port:
            return url
        return f"{use_scheme}://{WIDGET_HOST}:{use_port}{rest or ''}"
    return url


def build_widget(
    widget_type: str,
    catalog: dict[str, dict],
    secrets: dict,
    container_name: str,
    host_port: str | None,
    existing_widget: dict | None = None,
) -> tuple[dict | None, str | None]:
    """
    Build a Homepage service widget from docs catalog + secrets.
    Returns (widget, skip_reason).
    """
    meta = catalog.get(widget_type) or {}
    required = list(meta.get("required") or [])
    if widget_type == "portainer":
        scheme = meta.get("default_scheme") or "https"
    else:
        scheme = meta.get("default_scheme") or "http"

    merged_secrets = secrets_for(widget_type, container_name, secrets)
    widget: dict = {"type": widget_type}

    if existing_widget and isinstance(existing_widget, dict):
        # Start from curated widget, then normalize.
        widget = {**existing_widget, "type": existing_widget.get("type") or widget_type}

    # Secrets overlay (never log these)
    for key, value in merged_secrets.items():
        if key in ("url", "type"):
            continue
        widget[key] = value

    url = rewrite_widget_url(
        widget.get("url") or merged_secrets.get("url"),
        host_port,
        scheme=scheme if widget_type == "portainer" else (meta.get("default_scheme") or "http"),
    )
    if not url:
        url = widget_base_url(
            "https" if widget_type == "portainer" else (meta.get("default_scheme") or "http"),
            host_port,
        )
    if url:
        widget["url"] = url

    if widget_type == "portainer" and "fields" not in widget and meta.get("default_fields"):
        widget["fields"] = list(meta["default_fields"])

    missing = [k for k in required if k not in widget or widget.get(k) in (None, "")]
    if missing:
        return None, f"missing secrets: {', '.join(missing)}"

    if "url" not in widget:
        return None, "no widget url/port"

    return widget, None


def apply_widgets(
    fields: dict,
    container: dict,
    catalog: dict[str, dict],
    secrets: dict,
) -> tuple[dict, str | None]:
    """Attach or normalize a service widget on a service fields dict."""
    name = container["name"]
    image = container.get("image", "")
    ports = container.get("ports") or ""
    published = re.findall(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)->", ports)

    existing = fields.get("widget") if isinstance(fields.get("widget"), dict) else None
    widget_type = None
    if existing and existing.get("type"):
        widget_type = str(existing["type"])
    if not widget_type:
        widget_type = match_widget_type(image, name, catalog)

    host_port = None
    # Prefer port from service href (browser URL), then existing widget URL.
    if fields.get("href"):
        m = re.search(r":(\d+)(?:/|$)", str(fields["href"]))
        if m:
            host_port = m.group(1)
    if not host_port and existing and existing.get("url"):
        m = re.search(r":(\d+)(?:/|$)", str(existing["url"]))
        if m:
            host_port = m.group(1)
    if not host_port and published:
        if widget_type == "portainer" and "9443" in published:
            host_port = "9443"
        else:
            host_port = published[0]

    if not widget_type:
        if existing:
            fields = {k: v for k, v in fields.items() if k != "widget"}
            return fields, "removed unknown/stale widget"
        return fields, None

    widget, reason = build_widget(
        widget_type, catalog, secrets, name, host_port, existing_widget=existing
    )
    if widget is None:
        if "widget" in fields:
            fields = {k: v for k, v in fields.items() if k != "widget"}
        return fields, reason or "widget skipped"
    fields = dict(fields)
    fields["widget"] = widget
    return fields, f"widget:{widget_type}"

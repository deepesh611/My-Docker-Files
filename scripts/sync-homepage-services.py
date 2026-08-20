#!/usr/bin/env python3
"""Generate homepage/config/services.yaml from Docker containers + curated overrides."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from homepage_widget_docs import (  # noqa: E402
    apply_widgets,
    load_catalog,
    load_widget_secrets,
    refresh_widget_docs,
)

SKIP_CONTAINERS = {"homepage"}
DOCKER_SERVER = "my-docker"
OTHER_GROUP = "Other Containers"

ICON_MAP = (
    ("dozzle", "dozzle.png"),
    ("portainer", "portainer.png"),
    ("n8n", "n8n.png"),
    ("jellyfin", "jellyfin.png"),
    ("qdrant", "qdrant.png"),
    ("postgres", "postgres.png"),
    ("redis", "redis.png"),
    ("minio", "minio.png"),
    ("clickhouse", "clickhouse.png"),
    ("langfuse", "langfuse.png"),
    ("nginx", "nginx.png"),
)


def repo_defaults() -> tuple[Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    config_dir = Path(os.environ.get("CONFIG_DIR", repo_root / "homepage" / "config"))
    return repo_root, config_dir


def docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def list_containers() -> list[dict]:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}',
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    seen: set[str] = set()
    containers: list[dict] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < 5:
            parts.append("")
        names, image, status, ports, project = parts[:5]
        name = names.split(",")[0].lstrip("/")
        if not name or name in seen:
            continue
        seen.add(name)
        containers.append(
            {
                "name": name,
                "image": image,
                "status": status,
                "ports": ports,
                "project": project.strip(),
            }
        )
    return containers


def short_image(image: str) -> str:
    base = image.split("/")[-1]
    return base.split(":")[0] or image


def pick_icon(image: str, name: str) -> str:
    hay = f"{image} {name}".lower()
    for needle, icon in ICON_MAP:
        if needle in hay:
            return icon
    return "docker.png"


def first_host_port(ports: str) -> str | None:
    if not ports:
        return None
    match = re.search(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)->", ports)
    if match:
        return match.group(1)
    match = re.search(r":(\d+)->", ports)
    return match.group(1) if match else None


def host_port_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r":(\d+)(?:/|$)", str(href))
    return match.group(1) if match else None


def state_label(status: str) -> str:
    status_l = status.lower()
    if status_l.startswith("up"):
        return "Up"
    if status_l.startswith("exited"):
        return "Exited"
    if status_l.startswith("restarting"):
        return "Restarting"
    if status_l.startswith("created"):
        return "Created"
    if status_l.startswith("paused"):
        return "Paused"
    return status.split(" ")[0] if status else "Unknown"


def display_title(name: str) -> str:
    return re.sub(r"-\d+$", "", name)


def yaml_escape(value: str) -> str:
    if value == "":
        return '""'
    if re.search(r'[:#{}[\],&*?|<>=!%@`"\']|^\s|\s$', value) or value.lower() in {
        "true",
        "false",
        "null",
        "yes",
        "no",
    }:
        return json.dumps(value)
    return value


def yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if value is None:
        return "null"
    return yaml_escape(str(value))


def _coerce_scalar(value: str):
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


def parse_curated(text: str) -> list[tuple[str, list[tuple[str, dict]]]]:
    """Parse Homepage services-shaped YAML into [(group, [(title, fields)])]."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
    while i < len(lines) and (
        not lines[i].strip() or lines[i].lstrip().startswith("#")
    ):
        i += 1

    groups: list[tuple[str, list[tuple[str, dict]]]] = []
    current_group: str | None = None
    current_services: list[tuple[str, dict]] = []
    current_title: str | None = None
    current_fields: dict = {}
    nest_stack: list[tuple[int, list[str]]] = []

    def flush_service() -> None:
        nonlocal current_title, current_fields, nest_stack
        if current_title is not None:
            current_services.append((current_title, current_fields))
        current_title = None
        current_fields = {}
        nest_stack = []

    def flush_group() -> None:
        nonlocal current_group, current_services
        flush_service()
        if current_group is not None and current_services:
            groups.append((current_group, current_services))
        current_group = None
        current_services = []

    group_re = re.compile(r"^- (\S.*?):\s*$")
    service_re = re.compile(r"^    - (.+?):\s*$")
    field_re = re.compile(r"^(\s+)([A-Za-z0-9_]+):\s*(.*?)\s*$")

    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue

        gm = group_re.match(raw)
        if gm:
            flush_group()
            current_group = gm.group(1).strip()
            continue

        sm = service_re.match(raw)
        if sm and current_group is not None:
            flush_service()
            current_title = sm.group(1).strip()
            continue

        fm = field_re.match(raw)
        if fm and current_title is not None:
            indent, key, value = fm.groups()
            indent_n = len(indent)
            if indent_n < 8:
                continue
            if indent_n == 8:
                nest_stack = []
                if value == "":
                    current_fields[key] = {}
                    nest_stack = [(8, [key])]
                else:
                    current_fields[key] = _coerce_scalar(value)
                continue
            while nest_stack and indent_n <= nest_stack[-1][0]:
                nest_stack.pop()
            if not nest_stack:
                continue
            parent = current_fields
            for path_key in nest_stack[-1][1]:
                parent = parent.setdefault(path_key, {})
                if not isinstance(parent, dict):
                    break
            else:
                if value == "":
                    parent[key] = {}
                    nest_stack.append((indent_n, nest_stack[-1][1] + [key]))
                else:
                    parent[key] = _coerce_scalar(value)
            continue

    flush_group()
    return groups


def curated_index(
    groups: list[tuple[str, list[tuple[str, dict]]]],
) -> tuple[dict[str, tuple[str, str, dict]], list[str]]:
    """Map container name -> (group, title, fields). Drops incomplete/duplicate curated rows."""
    index: dict[str, tuple[str, str, dict]] = {}
    dropped: list[str] = []
    for group_name, services in groups:
        for title, fields in services:
            cname = str(fields.get("container", "")).strip()
            if not cname:
                dropped.append(f"curated {group_name}/{title} (no container)")
                continue
            if cname in index:
                dropped.append(f"curated {group_name}/{title} ({cname} duplicate curated)")
                continue
            index[cname] = (group_name, title, fields)
    return index, dropped


def auto_fields(container: dict) -> dict:
    fields: dict = {
        "icon": pick_icon(container["image"], container["name"]),
        "description": f"{short_image(container['image'])} · {state_label(container['status'])}",
        "server": DOCKER_SERVER,
        "container": container["name"],
        "showStats": True,
    }
    host_port = first_host_port(container["ports"])
    if host_port:
        fields["href"] = f"http://localhost:{host_port}"
    return fields


def emit_fields(fields: dict, indent: int = 8) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for key, value in fields.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(emit_fields(value, indent + 2))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{pad}  -")
                    lines.extend(emit_fields(item, indent + 4))
                else:
                    lines.append(f"{pad}  - {yaml_scalar(item)}")
        else:
            lines.append(f"{pad}{key}: {yaml_scalar(value)}")
    return lines


def unique_title(group: str, preferred: str, container_name: str, used: dict[str, set[str]]) -> str:
    titles = used.setdefault(group, set())
    title = preferred
    if title in titles:
        title = container_name
    base = title
    n = 2
    while title in titles:
        title = f"{base}-{n}"
        n += 1
    titles.add(title)
    return title


def build_services_yaml(
    curated_text: str,
    containers: list[dict],
    *,
    catalog: dict | None = None,
    secrets: dict | None = None,
) -> tuple[str, dict]:
    existing = {c["name"]: c for c in containers}
    curated_groups = parse_curated(curated_text)
    overrides, dropped = curated_index(curated_groups)
    catalog = catalog or {}
    secrets = secrets or {}
    widget_notes: list[str] = []

    for cname, (group_name, title, _fields) in list(overrides.items()):
        if cname in SKIP_CONTAINERS:
            dropped.append(f"{group_name}/{title} ({cname} skipped)")
            del overrides[cname]
            continue
        if cname not in existing:
            dropped.append(f"{group_name}/{title} ({cname} missing)")
            del overrides[cname]

    # group -> list of (title, fields)
    grouped: OrderedDict[str, list[tuple[str, dict]]] = OrderedDict()
    used_titles: dict[str, set[str]] = {}
    claimed_names: set[str] = set()
    claimed_ports: set[str] = set()
    curated_count = 0
    auto_count = 0
    widgets_attached = 0

    # Stable group order: curated group order first, then compose projects, then Other.
    curated_group_order = [g for g, _ in curated_groups]

    def ensure_group(name: str) -> None:
        if name not in grouped:
            grouped[name] = []

    def with_widget(fields: dict, container: dict) -> dict:
        nonlocal widgets_attached
        out, note = apply_widgets(fields, container, catalog, secrets)
        if note:
            widget_notes.append(f"{container['name']}: {note}")
        if isinstance(out.get("widget"), dict):
            widgets_attached += 1
        return out

    # 1) Curated overrides for containers that exist (one card each).
    for cname, (group_name, title, fields) in overrides.items():
        ensure_group(group_name)
        title = unique_title(group_name, title, cname, used_titles)
        out_fields = dict(fields)
        out_fields["container"] = cname
        out_fields.setdefault("server", DOCKER_SERVER)
        out_fields = with_widget(out_fields, existing[cname])
        grouped[group_name].append((title, out_fields))
        claimed_names.add(cname)
        port = host_port_from_href(out_fields.get("href"))
        if port:
            claimed_ports.add(port)
        curated_count += 1

    # 2) Auto cards for everything else (skip missing/skipped/claimed name or port).
    for container in sorted(containers, key=lambda c: (c["project"] or OTHER_GROUP, c["name"].lower())):
        name = container["name"]
        if name in SKIP_CONTAINERS or name in claimed_names:
            continue
        port = first_host_port(container["ports"])
        if port and port in claimed_ports:
            dropped.append(f"auto {name} (port {port} already used)")
            continue
        group_name = container["project"] if container["project"] else OTHER_GROUP
        ensure_group(group_name)
        title = unique_title(group_name, display_title(name), name, used_titles)
        fields = with_widget(auto_fields(container), container)
        grouped[group_name].append((title, fields))
        claimed_names.add(name)
        if port:
            claimed_ports.add(port)
        auto_count += 1

    # Emit with curated group order, then remaining alpha, Other last.
    remaining = [g for g in grouped if g not in curated_group_order and g != OTHER_GROUP]
    ordered_groups = [g for g in curated_group_order if g in grouped]
    ordered_groups.extend(sorted(remaining))
    if OTHER_GROUP in grouped:
        ordered_groups.append(OTHER_GROUP)

    lines: list[str] = [
        "# AUTO-GENERATED by scripts/sync-homepage-services.sh — do not edit.",
        "# Edit services.curated.yaml for overrides; widget-secrets.yaml for API keys.",
        "# Missing containers/duplicates removed; widgets configured from Homepage docs.",
        "",
    ]
    for group_name in ordered_groups:
        lines.append(f"- {group_name}:")
        for title, fields in grouped[group_name]:
            lines.append(f"    - {title}:")
            lines.extend(emit_fields(fields, 8))
            lines.append("")
        lines.append("")

    stats = {
        "curated": curated_count,
        "auto": auto_count,
        "dropped": dropped,
        "cards": len(claimed_names),
        "widgets": widgets_attached,
        "widget_notes": widget_notes,
    }
    return "\n".join(lines).rstrip() + "\n", stats


def atomic_write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def main() -> int:
    _, config_dir = repo_defaults()
    curated_path = config_dir / "services.curated.yaml"
    services_path = config_dir / "services.yaml"
    refresh_docs = (
        "--refresh-docs" in sys.argv
        or os.environ.get("HOMEPAGE_REFRESH_DOCS", "").lower() in {"1", "true", "yes"}
    )

    if not docker_available():
        print("docker unavailable; skipping sync", file=sys.stderr)
        return 0

    if not curated_path.is_file():
        print(f"missing curated file: {curated_path}", file=sys.stderr)
        return 1

    if refresh_docs:
        _catalog, msg = refresh_widget_docs(config_dir)
        print(msg)
    catalog = load_catalog(config_dir)
    secrets = load_widget_secrets(config_dir)

    curated_text = curated_path.read_text(encoding="utf-8")
    try:
        containers = list_containers()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"docker ps failed; skipping sync: {exc}", file=sys.stderr)
        return 0

    content, stats = build_services_yaml(
        curated_text, containers, catalog=catalog, secrets=secrets
    )
    changed = atomic_write_if_changed(services_path, content)
    action = "updated" if changed else "unchanged"
    print(
        f"homepage services {action}: "
        f"{len(containers)} containers seen, "
        f"{stats['cards']} cards "
        f"({stats['curated']} curated, {stats['auto']} auto), "
        f"{stats['widgets']} widgets, "
        f"{len(stats['dropped'])} dropped"
    )
    for item in stats["dropped"]:
        print(f"  dropped: {item}")
    for note in stats.get("widget_notes") or []:
        # Notes never include secret values
        print(f"  widget: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Resolve and apply newer component builds within source.json version families."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_INDEX_URL = (
    "https://artifacts.eng.sima.ai/artifactory/api/pypi/"
    "sima-pypi-group/simple"
)
DEFAULT_ARTIFACTORY_URL = "https://artifacts.eng.sima.ai/artifactory"
SUPPORTED_ARCHES = ("x86_64", "aarch64")
PLATFORM_ARGS = {
    "x86_64": (
        "manylinux_2_28_x86_64",
        "manylinux_2_27_x86_64",
        "manylinux2014_x86_64",
        "linux_x86_64",
    ),
    "aarch64": (
        "manylinux_2_28_aarch64",
        "manylinux_2_27_aarch64",
        "manylinux2014_aarch64",
        "linux_aarch64",
    ),
}
MLA_ARCH_SUFFIX = {"x86_64": "x86", "aarch64": "aarch64"}

PYTHON_VERSION_RE = re.compile(
    r"^(?P<base>\d+\.\d+\.\d+)\.dev(?P<dev>\d+)"
    r"\+(?P<channel>[A-Za-z0-9_-]+)\.(?P<build>\d+)$"
)
BINARY_VERSION_RE = re.compile(
    r"^(?P<base>v\d+\.\d+\.\d+)-"
    r"(?P<channel>[A-Za-z0-9_-]+)\.(?P<build>\d+)$"
)


class UpdateError(RuntimeError):
    """Raised when component resolution cannot be completed safely."""


@dataclass(frozen=True)
class VersionFamily:
    prefix: str
    build: int | tuple[int, int]
    pattern: re.Pattern[str]

    def parse_candidate(self, value: str) -> int | tuple[int, int] | None:
        match = self.pattern.fullmatch(value)
        if match is None:
            return None
        if "revision" in match.groupdict():
            return (int(match.group("revision")), int(match.group("build")))
        return int(match.group("build"))


@dataclass(frozen=True)
class Component:
    component_id: str
    kind: str
    name: str
    current: str
    version_prefix: str | None = None
    channel: str | None = None


def normalize_package_name(name: str) -> str:
    name = name.split("[", 1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def python_family(version: str) -> VersionFamily | None:
    match = PYTHON_VERSION_RE.fullmatch(version)
    if not match:
        return None
    prefix = (
        f"{match.group('base')}.dev{match.group('dev')}"
        f"+{match.group('channel')}."
    )
    return VersionFamily(
        prefix=prefix,
        build=int(match.group("build")),
        pattern=re.compile(rf"^{re.escape(prefix)}(?P<build>\d+)$"),
    )


def mla_release_family(prefix: str, channel: str) -> VersionFamily | None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+-", prefix) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", channel):
        return None
    return VersionFamily(
        prefix, (-1, -1),
        re.compile(rf"{re.escape(prefix)}(?P<revision>\d+)-{re.escape(channel)}\.(?P<build>\d+)"),
    )


def binary_family(version: str) -> VersionFamily | None:
    release = re.fullmatch(
        r"(?P<prefix>v\d+\.\d+\.\d+-)\d+-(?P<channel>[A-Za-z][A-Za-z0-9_-]*)\.\d+", version
    )
    if release:
        family = mla_release_family(release.group("prefix"), release.group("channel"))
        return VersionFamily(family.prefix, family.parse_candidate(version), family.pattern)
    match = BINARY_VERSION_RE.fullmatch(version)
    if not match:
        return None
    prefix = f"{match.group('base')}-{match.group('channel')}."
    return VersionFamily(
        prefix=prefix,
        build=int(match.group("build")),
        pattern=re.compile(rf"^{re.escape(prefix)}(?P<build>\d+)$"),
    )


def component_family(component: Component) -> VersionFamily:
    parser = python_family if component.kind == "python" else binary_family
    if component.channel is not None:
        family = mla_release_family(component.version_prefix or "", component.channel)
        if family is not None:
            current_build = family.parse_candidate(component.current)
            family = VersionFamily(
                family.prefix, (-1, -1) if current_build is None else current_build, family.pattern
            )
    elif component.version_prefix is None:
        family = parser(component.current)
    else:
        family = parser(component.version_prefix + "0")
        if family is not None:
            current_build = family.parse_candidate(component.current)
            if current_build is None:
                current_build = (-1, -1) if isinstance(family.build, tuple) else -1
            family = VersionFamily(family.prefix, current_build, family.pattern)
    if family is None:
        raise UpdateError(f"invalid version prefix for {component.name}")
    return family


def policy_label(component: Component) -> str:
    family = component_family(component)
    if component.channel is not None:
        return f"{family.prefix}*-{component.channel}.*"
    return f"{family.prefix}*"


def update_policy(doc: dict[str, Any]) -> dict[tuple[str, str], tuple[str, str | None]] | None:
    """Absent policy preserves legacy pin-derived behavior; an empty policy manages none."""
    if "component-updates" not in doc:
        return None
    policy = doc["component-updates"]
    if not isinstance(policy, dict) or set(policy) - {"python-packages", "binary-packages"}:
        raise UpdateError("invalid component-updates configuration")
    result = {}
    for section, kind, parser in (
        ("python-packages", "python", python_family),
        ("binary-packages", "binary", binary_family),
    ):
        entries = policy.get(section, {})
        if not isinstance(entries, dict):
            raise UpdateError(f"component-updates.{section} must be an object")
        for name, entry in entries.items():
            if not isinstance(entry, dict) or set(entry) not in ({"version-prefix"}, {"version-prefix", "channel"}):
                raise UpdateError(f"{name}: expected version-prefix and optional binary channel")
            prefix = entry["version-prefix"]
            channel = entry.get("channel")
            if "channel" in entry:
                valid = (
                    kind == "binary" and name.strip("/") == "mla/toolchain/mla-toolchain"
                    and isinstance(prefix, str) and isinstance(channel, str)
                    and mla_release_family(prefix, channel) is not None
                )
            else:
                valid = isinstance(prefix, str) and prefix.endswith(".") and parser(prefix + "0") is not None
            if not valid:
                raise UpdateError(f"{name}: invalid version-prefix/channel {entry!r}")
            normalized = normalize_package_name(name) if kind == "python" else name.strip("/")
            key = (kind, normalized)
            if key in result:
                raise UpdateError(f"duplicate update policy for {normalized}")
            result[key] = (prefix, channel)
    return result


def validate_manifest_policy(doc: dict[str, Any]) -> None:
    policy = update_policy(doc)
    if policy is None:
        return
    seen: dict[tuple[str, str], set[str]] = {}
    sections = [doc] + [doc[a] for a in SUPPORTED_ARCHES if isinstance(doc.get(a), dict)]
    for section in sections:
        for name, version in section.get("dependency_overrides", {}).items():
            seen.setdefault(("python", normalize_package_name(name)), set()).add(version)
        for field, kind in (("python-packages", "python"), ("binary-packages", "binary")):
            for item in section.get(field, []):
                if not isinstance(item, dict) or "version" not in item or "url" in item or "file" in item:
                    continue
                name = normalize_package_name(item["name"]) if kind == "python" else item["name"].strip("/")
                seen.setdefault((kind, name), set()).add(item["version"])
    for key in policy:
        versions = seen.get(key, set())
        if not versions:
            raise UpdateError(f"update policy has no editable pin for {key[1]}")
        if len(versions) != 1:
            raise UpdateError(f"conflicting duplicate pins for {key[1]}")
        parser = python_family if key[0] == "python" else binary_family
        if parser(next(iter(versions))) is None:
            raise UpdateError(f"unsupported current version for {key[1]}")


def effective_doc(doc: dict[str, Any], target_arch: str) -> dict[str, Any]:
    arch_doc = doc.get(target_arch)
    return arch_doc if isinstance(arch_doc, dict) else doc


def collect_components(doc: dict[str, Any], target_arch: str) -> list[Component]:
    validate_manifest_policy(doc)
    selected = effective_doc(doc, target_arch)
    components: dict[str, Component] = {}

    overrides = doc.get("dependency_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    else:
        overrides = dict(overrides)
    arch_overrides = selected.get("dependency_overrides", {})
    if selected is not doc and isinstance(arch_overrides, dict):
        overrides.update(arch_overrides)
    if isinstance(overrides, dict):
        for name, version in overrides.items():
            if not isinstance(version, str) or python_family(version) is None:
                continue
            normalized = normalize_package_name(str(name))
            component = Component(
                component_id=f"python:{normalized}:{version}",
                kind="python",
                name=normalized,
                current=version,
            )
            components[component.component_id] = component

    packages = selected.get("python-packages", doc.get("python-packages", []))
    if isinstance(packages, list):
        for item in packages:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            version = item.get("version")
            if (
                not isinstance(name, str)
                or not isinstance(version, str)
                or isinstance(item.get("url"), str)
                or isinstance(item.get("file"), str)
                or python_family(version) is None
            ):
                continue
            normalized = normalize_package_name(name)
            component = Component(
                component_id=f"python:{normalized}:{version}",
                kind="python",
                name=normalized,
                current=version,
            )
            components[component.component_id] = component

    binaries = selected.get("binary-packages", doc.get("binary-packages", []))
    if isinstance(binaries, list):
        for item in binaries:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            version = item.get("version")
            if (
                not isinstance(name, str)
                or not isinstance(version, str)
                or binary_family(version) is None
            ):
                continue
            clean_name = name.strip().strip("/")
            component = Component(
                component_id=f"binary:{clean_name}:{version}",
                kind="binary",
                name=clean_name,
                current=version,
            )
            components[component.component_id] = component

    policy = update_policy(doc)
    if policy is not None:
        components = {
            key: Component(c.component_id, c.kind, c.name, c.current, *policy[(c.kind, c.name)])
            for key, c in components.items() if (c.kind, c.name) in policy
        }
    return sorted(components.values(), key=lambda item: item.component_id)


def curl_text(url: str, *, head: bool = False) -> str:
    command = ["curl", "-fsSL", "--netrc-optional", "--connect-timeout", "15", "--max-time", "120"]
    if head:
        command.extend(["--head", "--output", "/dev/null"])
    command.append(url)
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip() or f"curl exited with {result.returncode}"
        raise UpdateError(f"failed to query {url}: {detail}")
    return result.stdout


def python_index_versions(
    package: str,
    current: str,
    index_url: str,
    family: VersionFamily | None = None,
) -> list[str]:
    family = family or python_family(current)
    if family is None:
        return []
    package_url = f"{index_url.rstrip('/')}/{normalize_package_name(package)}/"
    page = urllib.parse.unquote(html.unescape(curl_text(package_url)))
    page_pattern = re.compile(
        rf"(?<![A-Za-z0-9.]){re.escape(family.prefix)}(?P<build>\d+)(?=[-\"<>\s]|$)"
    )
    matches = {
        match.group(0)
        for match in page_pattern.finditer(page)
        if int(match.group("build")) > family.build
    }
    return sorted(
        matches,
        key=lambda value: family.parse_candidate(value) or -1,
        reverse=True,
    )


def wheel_is_available(
    package: str,
    version: str,
    *,
    target_arch: str,
    python_version: str,
    index_url: str,
) -> bool:
    with tempfile.TemporaryDirectory(prefix="component-wheel-check-") as output_dir:
        command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            "--only-binary=:all:",
            "--index-url",
            index_url,
            "--dest",
            output_dir,
        ]
        for platform in PLATFORM_ARGS[target_arch]:
            command.extend(["--platform", platform])
        command.extend(
            [
                "--implementation",
                "cp",
                "--abi",
                f"cp{python_version}",
                "--python-version",
                python_version,
                f"{package}=={version}",
            ]
        )
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            return True
        # Only an ordinary missing compatible distribution is a negative result.
        # Authentication, retries, and transport failures must fail the scan.
        diagnostic = result.stdout + result.stderr
        missing = "No matching distribution found" in diagnostic
        infrastructure = any(marker in diagnostic for marker in (
            "Retrying", "401", "403", "ConnectionError", "SSLError",
            "ReadTimeout", "Could not fetch URL", "Traceback",
        ))
        if missing and not infrastructure:
            return False
        raise UpdateError(f"wheel availability check failed for {package}=={version}; check index access")


def binary_index_versions(
    component: Component,
    *,
    target_arch: str,
    artifactory_url: str,
) -> list[str]:
    family = component_family(component)
    if family is None:
        return []
    parent, _, leaf = component.name.rpartition("/")
    if not parent:
        raise UpdateError(
            f"binary package {component.name!r} must include its Artifactory path"
        )
    if leaf != "mla-toolchain":
        raise UpdateError(
            f"automatic binary resolution is not implemented for {component.name!r}"
        )
    query = (
        f"{artifactory_url.rstrip('/')}/api/storage/{parent}"
        "?list&deep=0&listFolders=0"
    )
    try:
        listing = json.loads(curl_text(query))
    except json.JSONDecodeError as exc:
        raise UpdateError(f"Artifactory returned invalid JSON for {component.name}") from exc

    archive_suffix = MLA_ARCH_SUFFIX[target_arch]
    filename_re = re.compile(
        rf"^/?{re.escape(leaf)}-(?P<version>.+)"
        rf"-{re.escape(archive_suffix)}-ubuntu\.(?:zip)$"
    )
    versions = set()
    for item in listing.get("files", []):
        uri = item.get("uri") if isinstance(item, dict) else None
        if not isinstance(uri, str):
            continue
        match = filename_re.fullmatch(uri)
        if not match:
            continue
        version = match.group("version")
        build = family.parse_candidate(version)
        if build is not None and build > family.build:
            versions.add(version)
    return sorted(
        versions,
        key=lambda value: family.parse_candidate(value) or -1,
        reverse=True,
    )


def scan(
    source_json: Path,
    *,
    target_arch: str,
    output: Path,
    index_url: str,
    artifactory_url: str,
    max_candidates: int,
    newest_only: bool = False,
) -> None:
    doc = json.loads(source_json.read_text(encoding="utf-8"))
    python_version = re.sub(
        r"^(\d+)\.(\d+)(?:\.\d+)?$", r"\1\2", str(doc.get("python_version", "3.12"))
    )
    report: dict[str, Any] = {
        "target_arch": target_arch,
        "source_json": str(source_json),
        "source_sha256": hashlib.sha256(source_json.read_bytes()).hexdigest(),
        "components": {},
    }

    for component in collect_components(doc, target_arch):
        if component.kind == "python":
            candidates = python_index_versions(
                component.name, component.current, index_url, component_family(component)
            )
            if max_candidates > 0:
                candidates = candidates[:max_candidates]
            available = []
            for version in candidates:
                if wheel_is_available(
                    component.name,
                    version,
                    target_arch=target_arch,
                    python_version=python_version,
                    index_url=index_url,
                ):
                    available.append(version)
                    if newest_only:
                        break
        else:
            available = binary_index_versions(
                component,
                target_arch=target_arch,
                artifactory_url=artifactory_url,
            )
            if max_candidates > 0:
                available = available[:max_candidates]
        report["components"][component.component_id] = {
            "kind": component.kind,
            "name": component.name,
            "current": component.current,
            "version_prefix": component_family(component).prefix,
            "channel": component.channel,
            "coordinate": (
                f"{index_url.rstrip('/')}/{component.name}/" if component.kind == "python"
                else f"{artifactory_url.rstrip('/')}/{component.name}"
            ),
            "available": available,
        }
        newest = available[0] if available else "none"
        print(
            f"[{target_arch}] {component.name}: current={component.current}, "
            f"newest-compatible={newest}",
            flush=True,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def component_version_paths(
    doc: dict[str, Any],
    component: Component,
) -> set[tuple[str | int, ...]]:
    paths: set[tuple[str | int, ...]] = set()
    sections = [((), doc)]
    sections.extend(
        ((key,), value)
        for key, value in doc.items()
        if isinstance(value, dict)
    )
    for section_path, section in sections:
        if component.kind == "python":
            overrides = section.get("dependency_overrides", {})
            if isinstance(overrides, dict):
                for name, version in overrides.items():
                    if (
                        isinstance(name, str)
                        and normalize_package_name(name) == component.name
                        and version == component.current
                    ):
                        paths.add(section_path + ("dependency_overrides", name))

            packages = section.get("python-packages", [])
            if isinstance(packages, list):
                for index, item in enumerate(packages):
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("name"), str)
                        and normalize_package_name(item["name"]) == component.name
                        and item.get("version") == component.current
                        and not isinstance(item.get("url"), str)
                        and not isinstance(item.get("file"), str)
                    ):
                        paths.add(
                            section_path + ("python-packages", index, "version")
                        )
        else:
            packages = section.get("binary-packages", [])
            if isinstance(packages, list):
                for index, item in enumerate(packages):
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("name"), str)
                        and item["name"].strip().strip("/") == component.name
                        and item.get("version") == component.current
                        and not isinstance(item.get("url"), str)
                        and not isinstance(item.get("file"), str)
                    ):
                        paths.add(
                            section_path + ("binary-packages", index, "version")
                        )
    return paths


def json_string_spans(
    source_text: str,
) -> dict[tuple[str | int, ...], tuple[int, int, str]]:
    decoder = json.JSONDecoder()
    spans: dict[tuple[str | int, ...], tuple[int, int, str]] = {}

    def skip_whitespace(position: int) -> int:
        while position < len(source_text) and source_text[position].isspace():
            position += 1
        return position

    def parse(position: int, path: tuple[str | int, ...]) -> int:
        position = skip_whitespace(position)
        if source_text[position] == "{":
            position = skip_whitespace(position + 1)
            if source_text[position] == "}":
                return position + 1
            while True:
                key, position = decoder.raw_decode(source_text, position)
                if not isinstance(key, str):
                    raise UpdateError("JSON object key is not a string")
                position = skip_whitespace(position)
                if source_text[position] != ":":
                    raise UpdateError("invalid JSON object separator")
                position = parse(position + 1, path + (key,))
                position = skip_whitespace(position)
                if source_text[position] == "}":
                    return position + 1
                if source_text[position] != ",":
                    raise UpdateError("invalid JSON object delimiter")
                position = skip_whitespace(position + 1)
        if source_text[position] == "[":
            position = skip_whitespace(position + 1)
            if source_text[position] == "]":
                return position + 1
            index = 0
            while True:
                position = parse(position, path + (index,))
                index += 1
                position = skip_whitespace(position)
                if source_text[position] == "]":
                    return position + 1
                if source_text[position] != ",":
                    raise UpdateError("invalid JSON array delimiter")
                position = skip_whitespace(position + 1)
        start = position
        value, position = decoder.raw_decode(source_text, position)
        if isinstance(value, str):
            spans[path] = (start, position, value)
        return position

    end = skip_whitespace(parse(0, ()))
    if end != len(source_text):
        raise UpdateError("unexpected content after source JSON")
    return spans


def select_updates(
    source_doc: dict[str, Any],
    reports: list[dict[str, Any]],
) -> dict[str, str]:
    if not reports:
        raise UpdateError("at least one architecture scan report is required")
    report_by_arch: dict[str, dict[str, Any]] = {}
    for report in reports:
        arch = str(report.get("target_arch"))
        if arch not in SUPPORTED_ARCHES:
            raise UpdateError(f"unsupported scan report architecture: {arch!r}")
        if arch in report_by_arch:
            raise UpdateError(f"duplicate scan report for architecture: {arch}")
        report_by_arch[arch] = report

    components_by_arch = {
        arch: {
            component.component_id: component
            for component in collect_components(source_doc, arch)
        }
        for arch in report_by_arch
    }
    source_components = {
        component_id: component
        for arch_components in components_by_arch.values()
        for component_id, component in arch_components.items()
    }
    updates: dict[str, str] = {}
    for component_id, component in sorted(source_components.items()):
        required_arches = [
            arch
            for arch, arch_components in components_by_arch.items()
            if component_id in arch_components
        ]
        entries = [
            report_by_arch[arch].get("components", {}).get(component_id)
            for arch in required_arches
        ]
        if any(not isinstance(entry, dict) for entry in entries):
            raise UpdateError(f"scan report is missing component {component_id}")
        common = set(entries[0].get("available", []))
        for entry in entries[1:]:
            common.intersection_update(entry.get("available", []))
        family = component_family(component)
        if family is None:
            continue
        eligible = [
            value
            for value in common
            if family.parse_candidate(value) is not None
            and family.parse_candidate(value) > family.build
        ]
        if eligible:
            updates[component_id] = max(
                eligible, key=lambda value: family.parse_candidate(value) or -1
            )
    return updates


def apply_updates_preserving_format(
    source_text: str,
    source_doc: dict[str, Any],
    components: dict[str, Component],
    updates: dict[str, str],
) -> str:
    replacements: dict[tuple[str | int, ...], str] = {}
    for component_id, new_version in updates.items():
        component = components[component_id]
        paths = component_version_paths(source_doc, component)
        if not paths:
            raise UpdateError(f"cannot locate manifest fields for {component_id}")
        for path in paths:
            previous = replacements.setdefault(path, new_version)
            if previous != new_version:
                raise UpdateError(f"ambiguous updates for manifest path {path}")

    spans = json_string_spans(source_text)
    edits: list[tuple[int, int, str]] = []
    for path, new_version in replacements.items():
        span = spans.get(path)
        if span is None:
            raise UpdateError(f"cannot locate JSON string at manifest path {path}")
        start, end, old_version = span
        component_ids = [
            component_id
            for component_id in updates
            if path in component_version_paths(source_doc, components[component_id])
        ]
        if not component_ids or all(
            components[component_id].current != old_version
            for component_id in component_ids
        ):
            raise UpdateError(f"unexpected version at manifest path {path}")
        edits.append((start, end, json.dumps(new_version)))

    for start, end, replacement in sorted(edits, reverse=True):
        source_text = source_text[:start] + replacement + source_text[end:]

    json.loads(source_text)
    return source_text


def merge(
    source_json: Path,
    report_paths: list[Path],
    *,
    output: Path,
    summary: Path,
) -> bool:
    source_text = source_json.read_text(encoding="utf-8")
    source_doc = json.loads(source_text)
    reports = [
        json.loads(report_path.read_text(encoding="utf-8"))
        for report_path in report_paths
    ]
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    mismatched_reports = [
        str(report_paths[index])
        for index, report in enumerate(reports)
        if report.get("source_sha256") != source_sha256
    ]
    if mismatched_reports:
        raise UpdateError(
            "scan reports were generated from a different source.json: "
            + ", ".join(mismatched_reports)
        )
    components = {
        component.component_id: component
        for arch in SUPPORTED_ARCHES
        for component in collect_components(source_doc, arch)
    }
    updates = select_updates(source_doc, reports)
    updated_text = apply_updates_preserving_format(
        source_text, source_doc, components, updates
    )
    output.write_text(updated_text, encoding="utf-8")

    lines = ["## Component version updates", ""]
    if updates:
        lines.extend(
            [
                "| Component | Previous | Updated | Update prefix |",
                "|---|---|---|---|",
            ]
        )
        for component_id, new_version in sorted(updates.items()):
            component = components[component_id]
            lines.append(
                f"| `{component.name}` | `{component.current}` | `{new_version}` | `{policy_label(component)}` |"
            )
    else:
        lines.append("No newer builds were available in the currently pinned version families.")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bool(updates)


def summarize(base: Path, updated: Path, output: Path) -> None:
    base_doc = json.loads(base.read_text(encoding="utf-8"))
    updated_doc = json.loads(updated.read_text(encoding="utf-8"))
    base_components = {
        (component.kind, component.name): component.current
        for arch in SUPPORTED_ARCHES
        for component in collect_components(base_doc, arch)
    }
    updated_components = {
        (component.kind, component.name): component.current
        for arch in SUPPORTED_ARCHES
        for component in collect_components(updated_doc, arch)
    }
    lines = [
        "## Component version updates",
        "",
        "| Component | Previous | Updated | Update prefix |",
        "|---|---|---|---|",
    ]
    changes = 0
    for key, old_version in sorted(base_components.items()):
        new_version = updated_components.get(key)
        if new_version and new_version != old_version:
            component = next(
                c for c in collect_components(base_doc, "aarch64") + collect_components(base_doc, "x86_64")
                if (c.kind, c.name) == key
            )
            family = component_family(component)
            build = family.parse_candidate(new_version)
            if build is None or build <= family.build:
                raise UpdateError(f"candidate violates update policy for {key[1]}")
            lines.append(f"| `{key[1]}` | `{old_version}` | `{new_version}` | `{policy_label(component)}` |")
            changes += 1
    components = {
        c.component_id: c for arch in SUPPORTED_ARCHES for c in collect_components(base_doc, arch)
    }
    updates = {
        c.component_id: updated_components[(c.kind, c.name)] for c in components.values()
        if updated_components.get((c.kind, c.name), c.current) != c.current
    }
    expected = json.loads(apply_updates_preserving_format(
        base.read_text(encoding="utf-8"), base_doc, components, updates,
    ))
    if expected != updated_doc:
        raise UpdateError("candidate changes fields outside managed version pins")
    if not changes:
        raise UpdateError("automation branch contains no managed component updates")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--newest-only", action="store_true", help="Stop at the newest compatible wheel; use for single-architecture reports")
    scan_parser.add_argument("--source-json", type=Path, required=True)
    scan_parser.add_argument("--target-arch", choices=SUPPORTED_ARCHES, required=True)
    scan_parser.add_argument("--output", type=Path, required=True)
    scan_parser.add_argument(
        "--index-url",
        default=os.environ.get("MODELSDK_PYPI_INDEX_URL", DEFAULT_INDEX_URL),
    )
    scan_parser.add_argument(
        "--artifactory-url",
        default=os.environ.get("ARTIFACTORY_BASE_URL", DEFAULT_ARTIFACTORY_URL),
    )
    scan_parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Maximum newer builds to validate per component; 0 checks all",
    )

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--source-json", type=Path, required=True)
    merge_parser.add_argument("--report", type=Path, action="append", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    merge_parser.add_argument("--summary", type=Path, required=True)
    merge_parser.add_argument("--github-output", type=Path)

    summary_parser = subparsers.add_parser("summarize")
    summary_parser.add_argument("--base", type=Path, required=True)
    summary_parser.add_argument("--updated", type=Path, required=True)
    summary_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "scan":
            scan(
                args.source_json,
                target_arch=args.target_arch,
                output=args.output,
                index_url=args.index_url,
                artifactory_url=args.artifactory_url,
                max_candidates=args.max_candidates,
                newest_only=args.newest_only,
            )
        elif args.command == "merge":
            changed = merge(
                args.source_json,
                args.report,
                output=args.output,
                summary=args.summary,
            )
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as handle:
                    handle.write(f"changed={'true' if changed else 'false'}\n")
        elif args.command == "summarize":
            summarize(args.base, args.updated, args.output)
    except (OSError, ValueError, UpdateError) as exc:
        print(f"component update failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

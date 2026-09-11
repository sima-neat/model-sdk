"""Report versions from the artifacts actually selected for a compiler bundle."""
from __future__ import annotations

import html
import re
from pathlib import Path

SIMA_COMPONENTS = {"mpk-parser", "neural-compressor", "mla-toolchain"}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.split("[", 1)[0]).lower()


def inventory(artifacts: list[Path], source: dict, arch: str) -> list[dict]:
    selected = source.get(arch, {})
    requested = {}
    for key in ("python-packages", "source-packages", "preload-packages"):
        for item in selected.get(key, source.get(key, [])):
            requested[normalize(item["name"])] = str(
                item.get("version") or item.get("vulcan", {}).get("policy")
                or item.get("vulcan", {}).get("ref") or "URL/file"
            )
    for section in (source, selected):
        requested.update({normalize(k): str(v) for k, v in section.get("dependency_overrides", {}).items()})
    binaries = {}
    for item in selected.get("binary-packages", source.get("binary-packages", [])):
        name = item["name"].rsplit("/", 1)[-1]
        extension = str(item.get("extension", "")).strip()
        archive_type = (
            extension.lstrip(".")
            if extension else str(item.get("archive-type", "zip")).strip() or "zip"
        )
        suffix = f'-{ {"aarch64": "aarch64", "x86_64": "x86"}[arch]}-ubuntu' if name == "mla-toolchain" and archive_type == "zip" else ""
        extension = "." + archive_type
        binaries[f'{name}-{item["version"]}{suffix}{extension}'] = (name, item["version"])
    rows = []
    for artifact in artifacts:
        filename = artifact.name
        if filename in binaries:
            name, version = binaries[filename]
            kind, wanted = "binary", version
        elif filename.endswith(".whl"):
            parts = filename[:-4].split("-")
            if len(parts) not in (5, 6):
                raise ValueError(f"Invalid wheel filename: {filename}")
            name, version = parts[:2]
            kind, wanted = "wheel", requested.get(normalize(name), "dependency")
        else:
            match = re.fullmatch(r"(.+)-(\d[^/]*)\.(?:tar\.gz|zip)", filename)
            if not match:
                raise ValueError(f"Cannot determine component version: {filename}")
            name, version = match.groups()
            kind, wanted = "source", requested.get(normalize(name), "dependency")
        rows.append({"name": normalize(name), "version": version, "requested": wanted,
                     "kind": kind, "artifact": filename})
    return sorted(rows, key=lambda row: (row["name"], row["version"], row["artifact"]))


def summary(rows: list[dict], *, version: str, arch: str, provenance: dict) -> str:
    def cell(value):
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")

    def table(items):
        lines = ["| Component | Resolved version | Requested | Type |", "|---|---|---|---|"]
        lines.extend("| " + " | ".join(cell(row[key]) for key in ("name", "version", "requested", "kind")) + " |" for row in items)
        return lines

    sima = [row for row in rows if row["name"].startswith("sima-") or row["name"] in SIMA_COMPONENTS]
    other = [row for row in rows if row not in sima]
    lines = [f"## Model Compiler — {cell(arch)}", "", f"Bundle: **{cell(version)}**", "",
             "Versions below come from the selected bundle artifacts.", "", "### SiMa components", "", *table(sima)]
    if other:
        lines += ["", "<details>", f"<summary>All other bundled components ({len(other)})</summary>", "", *table(other), "", "</details>"]
    llima = provenance.get("sima_lmm")
    if llima:
        lines += ["", f'LLiMa resolved commit: {cell(llima.get("resolved-commit", "unknown"))}', ""]
    return "\n".join(lines) + "\n"

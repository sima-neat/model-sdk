#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import zipfile

from component_inventory import inventory, summary


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def binary_archive_names(source_manifest: Path, target_arch: str) -> set[str]:
    if not source_manifest.is_file():
        return set()
    doc = json.loads(source_manifest.read_text(encoding="utf-8"))

    def names_from_items(items: object) -> set[str]:
        names = set()
        if not isinstance(items, list):
            return names
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip().strip("/")
            version = str(item.get("version", "")).strip()
            extension = str(item.get("extension", "")).strip()
            archive_type = str(item.get("archive-type", "zip")).strip() or "zip"
            if extension:
                archive_type = extension[1:] if extension.startswith(".") else extension
            if name and version:
                base = name.rsplit("/", 1)[-1]
                normalized = name.lower()
                if archive_type == "zip" and base == "mla-toolchain" and "mla" in normalized:
                    arch_suffix = {"x86_64": "x86", "aarch64": "aarch64"}.get(target_arch)
                    if not arch_suffix:
                        raise SystemExit(f"Unsupported MLA toolchain architecture: {target_arch!r}")
                    version = f"{version}-{arch_suffix}-ubuntu"
                names.add(f"{base}-{version}.{archive_type}")
        return names

    arch_doc = doc.get(target_arch)
    if isinstance(arch_doc, dict) and isinstance(arch_doc.get("binary-packages"), list):
        return names_from_items(arch_doc["binary-packages"])
    return names_from_items(doc.get("binary-packages", []))


def main() -> int:
    p = argparse.ArgumentParser(
        description="Generate metadata.json for sima-cli distribution from bundle artifacts."
    )
    p.add_argument("--artifacts-dir", required=True, help="Directory containing bundle artifacts.")
    p.add_argument("--output", required=True, help="Path for generated metadata.json.")
    p.add_argument("--name", default="sima-neat-model-compiler")
    p.add_argument("--version", required=True)
    p.add_argument("--release", default="stable")
    p.add_argument(
        "--description",
        default="SiMa.ai NEAT Model Compiler",
    )
    p.add_argument(
        "--host-os",
        default="linux",
        help="Host OS value for metadata platforms host entry (default: linux).",
    )
    p.add_argument(
        "--installer-script",
        default="install_modelsdk_wheels.sh",
        help="Installer script filename included in resources.",
    )
    p.add_argument("--target-arch", choices=["x86_64", "aarch64"], required=True)
    p.add_argument(
        "--source-manifest",
        default="source.json",
        help="Source manifest filename included in resources when present.",
    )
    p.add_argument(
        "--wheel-manifest",
        default="manifest.txt",
        help="Wheel manifest filename generated and included in resources.",
    )
    p.add_argument(
        "--archive-name",
        default="model-compiler-package.zip",
        help="Archive filename to create for the self-installing package.",
    )
    p.add_argument(
        "--resolved-packages",
        help="Optional JSON file containing resolved package provenance.",
    )
    p.add_argument(
        "--offline-output",
        help="Path for generated manual-distribution metadata (default: metadata-offline.json beside --output).",
    )
    args = p.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    if not artifacts_dir.is_dir():
        raise SystemExit(f"artifacts-dir does not exist: {artifacts_dir}")

    archive_name = args.archive_name.strip()
    if not archive_name or "/" in archive_name or archive_name.startswith("."):
        raise SystemExit(f"Invalid archive name: {args.archive_name!r}")
    if not archive_name.endswith(".zip"):
        raise SystemExit("archive name must end with .zip")

    source_manifest = artifacts_dir / args.source_manifest
    binary_names = binary_archive_names(source_manifest, args.target_arch)

    artifacts = sorted(
        [
            p
            for p in artifacts_dir.iterdir()
            if p.is_file()
            and (p.suffix in {".whl", ".zip"} or p.name.endswith(".tar.gz"))
            and p.name != archive_name
        ]
    )
    if not artifacts:
        raise SystemExit(f"No wheel or binary artifacts found in {artifacts_dir}")

    package_artifacts = [
        a
        for a in artifacts
        if a.suffix == ".whl"
        or a.name.endswith(".tar.gz")
        or (a.suffix == ".zip" and a.name not in binary_names)
    ]
    if not package_artifacts:
        raise SystemExit(f"No wheel or source package artifacts found in {artifacts_dir}")

    component_versions = inventory(
        artifacts, json.loads(source_manifest.read_text()) if source_manifest.is_file() else {}, args.target_arch
    )

    installer = artifacts_dir / args.installer_script
    if not installer.is_file():
        raise SystemExit(
            f"Installer script not found: {installer}. "
            f"Put it in artifacts-dir or override --installer-script."
        )

    wheel_manifest = artifacts_dir / args.wheel_manifest
    wheel_manifest.write_text(
        "".join(f"{a.name}\n" for a in package_artifacts),
        encoding="utf-8",
    )

    resources = [a.name for a in artifacts] + [wheel_manifest.name, installer.name]
    if source_manifest.is_file():
        resources.append(source_manifest.name)

    archive_path = artifacts_dir / archive_name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in resources:
            zf.write(artifacts_dir / name, arcname=name)
    for name in resources:
        path = artifacts_dir / name
        if path.exists():
            path.unlink()
    resources = [archive_name]

    checksums = {}
    total_download_bytes = 0
    for name in resources:
        f = artifacts_dir / name
        checksums[name] = sha256_file(f)
        total_download_bytes += f.stat().st_size

    install_script = (
        "bundle_dir=$(mktemp -d) && "
        "trap 'rm -rf \"$bundle_dir\"' EXIT && "
        f"unzip -oq {shlex.quote(archive_name)} -d \"$bundle_dir\" && "
        f"bash \"$bundle_dir\"/{shlex.quote(installer.name)}"
    )
    post_message = (
        "[bold green]Successfully installed Model Compiler.[/bold green]\n\n"
        "[bold]Virtual environment location:[/bold]\n"
        "The installer creates the venv at "
        "[green]/sdk-extensions/model-compiler[/green] when writable, "
        "otherwise it falls back to [green]/sdk-add-on/model-compiler[/green], "
        "or [green]~/sdk-extensions/model-compiler[/green].\n\n"
        "[bold]Reload your shell environment:[/bold]\n"
        "The installer updates [green]~/.bashrc[/green] when it exists, "
        "otherwise [green]~/.bash_profile[/green]. Run "
        "[green]source ~/.bashrc[/green] or [green]source ~/.bash_profile[/green], "
        "or log out and log back in. Then run [green]activate-model-compiler[/green] "
        "to activate the environment and [green]deactivate-model-compiler[/green] to leave it."
    )
    metadata = {
        "name": args.name,
        "version": args.version,
        "release": args.release,
        "description": args.description,
        "platforms": [
            {
                "type": "host",
                "os": [args.host_os],
            },
            {"type": "palette"},
        ],
        "resources": resources,
        "resources-checksum": checksums,
        "size": {
            "download": human_mb(total_download_bytes),
            "install": "9 GB",
        },
        "installation": {
            "script": install_script,
            "post-message": post_message,
        },
    }
    if args.resolved_packages:
        resolved_packages_path = Path(args.resolved_packages)
        if not resolved_packages_path.is_file():
            raise SystemExit(
                f"Resolved package provenance not found: {resolved_packages_path}"
            )
        resolved_packages = json.loads(
            resolved_packages_path.read_text(encoding="utf-8")
        )
        if not isinstance(resolved_packages, dict):
            raise SystemExit("Resolved package provenance must be a JSON object")
        metadata["resolved-packages"] = resolved_packages
    metadata["component-versions"] = component_versions
    (artifacts_dir / "component-versions.json").write_text(
        json.dumps(component_versions, indent=2) + "\n", encoding="utf-8"
    )
    (artifacts_dir / "component-versions.md").write_text(
        summary(component_versions, version=args.version, arch=args.target_arch,
                provenance=metadata.get("resolved-packages", {})), encoding="utf-8"
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    offline_output = (
        Path(args.offline_output)
        if args.offline_output
        else output.with_name("metadata-offline.json")
    )
    manual_metadata = dict(metadata)
    # This metadata only downloads the archive; its installer is run after the
    # archive is transferred to a supported Linux target. Allow users on any
    # common host OS to fetch it for manual distribution.
    manual_metadata["platforms"] = [
        {"type": "host", "os": ["linux", "mac", "windows"]},
        {"type": "palette"},
    ]
    manual_metadata["installation"] = {
        "script": (
            "echo 'Model Compiler archive downloaded. Transfer or distribute the ZIP as needed, then extract it and run: "
            f"bash ./{installer.name}'"
        ),
        "post-message": (
            "[bold green]Model Compiler archive downloaded.[/bold green]\n\n"
            f"The archive [green]{archive_name}[/green] contains the installer and all dependencies. "
            "Copy or distribute the ZIP as needed; on the target, extract it and run "
            f"[green]bash ./{installer.name}[/green]."
        ),
    }
    offline_output.parent.mkdir(parents=True, exist_ok=True)
    offline_output.write_text(
        json.dumps(manual_metadata, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

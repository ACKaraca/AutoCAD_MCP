"""Build locked real SDK references and package only the explicitly chosen observer profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "net48": ("24.0.0", "24.0.0", "R24.0", "R24.3"),
    "net8.0-windows": ("25.0.1", "25.0.0", "R25.0", "R25.1"),
    "net10.0-windows": ("25.0.2", "25.0.2", "R25.0", "R25.1"),
}


def package(profile: str, output: Path, destination: Path) -> Path:
    full, core, minimum, maximum = PROFILES[profile]
    names = ["ContextObserver.Host.dll", "ContextObserver.Core.dll"]
    if profile != "net48":
        names.append("ContextObserver.Host.deps.json")
    contents = {name: (output / name).read_bytes() for name in names}
    manifest = ElementTree.Element(
        "ApplicationPackage",
        {
            "SchemaVersion": "1.0",
            "AppVersion": "1.0.0",
            "Name": f"AutoCAD MCP Context Observer ({profile})",
            "ProductType": "Application",
            "ProductCode": "{4142AA19-C5D6-40C6-A7CD-D335468F1080}",
            "UpgradeCode": "{CA915832-D9AD-4D76-8135-C26DE01B45C2}",
        },
    )
    components = ElementTree.SubElement(manifest, "Components")
    entry = ElementTree.SubElement(
        components,
        "ComponentEntry",
        {
            "AppName": "ContextObserver",
            "AppType": ".Net",
            "ModuleName": "./Contents/ContextObserver.Host.dll",
            "LoadOnAutoCADStartup": "False",
            "LoadOnAppearance": "False",
            "LoadOnProxy": "False",
            "LoadOnCommandInvocation": "False",
        },
    )
    ElementTree.SubElement(
        entry,
        "RuntimeRequirements",
        {
            "OS": "Win64",
            "Platform": "AutoCAD",
            "SeriesMin": minimum,
            "SeriesMax": maximum,
        },
    )
    metadata = {
        "profile": profile,
        "sdk": "10.0.401",
        "configuration": "Release",
        "references": {"AutoCAD.NET": full, "AutoCAD.NET.Core": core, "AutoCAD.NET.Model": core},
        "host_execution": "unverified",
        "load": "explicit NETLOAD only",
        "sha256": {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
    }
    prefix = f"ContextObserver-{profile}.bundle/"
    files = {prefix + "Contents/" + name: data for name, data in contents.items()}
    files[prefix + "PackageContents.xml"] = ElementTree.tostring(manifest, encoding="utf-8")
    files[prefix + "build.json"] = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"ContextObserver-{profile}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/native-observer")
    args = parser.parse_args()
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise RuntimeError("the pinned .NET SDK is required")
    project = ROOT / "native/context-observer/Host/ContextObserver.Host.csproj"
    for command in (
        [dotnet, "restore", str(project), "--locked-mode"],
        [
            dotnet,
            "build",
            str(project),
            "--no-restore",
            "--configuration",
            "Release",
            "--framework",
            args.profile,
            "--verbosity",
            "minimal",
        ],
    ):
        subprocess.run(command, check=True, cwd=ROOT)  # noqa: S603 - fixed owned build with enum profile
    archive = package(args.profile, project.parent / "bin/Release" / args.profile, args.output)
    print(archive.name)  # noqa: T201 - maintainer CLI artifact name only


if __name__ == "__main__":
    main()

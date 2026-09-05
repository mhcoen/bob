#!/usr/bin/env python3
"""Build and exercise all workspace wheels outside the checkout (requires uv)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

PACKAGES = {
    "bob-tools": "bob_tools",
    "orchestra": "orchestra",
    "mcloop": "mcloop",
    "duplo": "duplo",
}
CLIS = ("bob", "bob-plan", "orchestra", "mcloop", "duplo")
ROOT = Path(__file__).resolve().parents[1]


def run(
    command: list[str], cwd: Path, log: Path, env: dict[str, str] | None = None
) -> bool:
    with log.open("w") as output:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    if result.returncode:
        print(f"FAIL: {log.name}\n{log.read_text()[-12000:]}", flush=True)
        return False
    print(f"PASS: {log.name}", flush=True)
    return True


def required_members(source: Path, module: str) -> list[str]:
    """Inventory runtime code and declarative assets independently of setuptools."""
    package = source / module
    return sorted(
        str(path.relative_to(source))
        for path in package.rglob("*")
        if path.is_file()
        and "tests" not in path.relative_to(package).parts
        and (
            path.suffix == ".py"
            or path.name == "py.typed"
            or ("workflows" in path.parts and path.suffix in {".orc", ".md", ".json"})
            or "resources" in path.relative_to(package).parts
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline", action="store_true", help="use only uv's cached dependencies"
    )
    parser.add_argument(
        "--work-dir", type=Path, help="new directory for preserved logs and artifacts"
    )
    args = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must be installed and on PATH")
    work = (
        args.work_dir.resolve()
        if args.work_dir
        else Path(tempfile.mkdtemp(prefix="bob-wheels-"))
    )
    if work.is_relative_to(ROOT):
        parser.error("--work-dir must be outside the checkout")
    if args.work_dir:
        work.mkdir(parents=True, exist_ok=False)
    print(f"Artifacts: {work}", flush=True)
    source = work / "source"
    source.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
    ignore = shutil.ignore_patterns(
        "build",
        "dist",
        "*.egg-info",
        ".venv",
        "__pycache__",
        ".git",
        ".scratch",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "logs",
        ".mcloop",
        ".duplo",
    )
    for name in PACKAGES:
        shutil.copytree(
            ROOT / "packages" / name, source / "packages" / name, ignore=ignore
        )
    wheels = work / "wheels"
    options = ["--offline"] if args.offline else []
    if not run(
        [
            uv,
            "build",
            "--all-packages",
            "--wheel",
            "--no-build-logs",
            "--out-dir",
            str(wheels),
            "--python",
            sys.executable,
            *options,
        ],
        source,
        work / "build.log",
    ):
        return 1

    failures: list[str] = []
    wheel_paths: list[Path] = []
    for name, module in PACKAGES.items():
        matches = list(wheels.glob(f"{name.replace('-', '_')}-*.whl"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one wheel for {name}, found {matches}")
        wheel = matches[0]
        wheel_paths.append(wheel)
        with ZipFile(wheel) as archive:
            missing = sorted(
                set(required_members(source / "packages" / name, module))
                - set(archive.namelist())
            )
        if missing:
            failure = f"{name}: missing wheel members: {', '.join(missing)}"
            failures.append(failure)
            print(f"FAIL: {failure}", flush=True)
        else:
            print(f"PASS: {name} runtime resource inventory", flush=True)

    # Provisioning may access the package index; runtime probes below cannot.
    venv = work / "venv"
    if not run(
        [uv, "venv", "--python", sys.executable, str(venv), *options],
        work,
        work / "venv.log",
    ):
        return 1
    python = venv / "bin" / "python"
    if not run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            *map(str, wheel_paths),
            *options,
        ],
        work,
        work / "install.log",
    ):
        return 1
    if not run(
        [uv, "pip", "freeze", "--python", str(python)],
        work,
        work / "installed-requirements.txt",
    ):
        return 1

    project = work / "project"
    project.mkdir()
    sandbox_home = work / "runtime-home"
    sandbox_home.mkdir()
    probe = project / "probe.py"
    shutil.copy2(Path(__file__).with_name("wheel_probe.py"), probe)
    # Do not pass provider credentials, Python import overrides, or active session
    # configuration to the runtime probes. Path.home is isolated inside the probe.
    env = {"PATH": str(venv / "bin") + os.pathsep + os.defpath, "LANG": "en_US.UTF-8"}
    probe_command = [str(python), "-I", str(probe), "--sandbox-home", str(sandbox_home)]
    for cli in CLIS:
        if not run(
            [*probe_command, "--cli", cli], project, work / f"cli-{cli}.log", env
        ):
            failures.append(f"{cli}: installed CLI help failed")
    if not run(probe_command, project, work / "runtime.log", env):
        failures.append("installed runtime checks failed")

    report = {
        "passed": not failures,
        "failures": failures,
        "wheels": [p.name for p in wheel_paths],
        "python": sys.version,
    }
    (work / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    for failure in failures:
        print(f"FAIL: {failure}", flush=True)
    print(
        f"{'PASS' if not failures else 'FAIL'}: wheel smoke; logs and artifacts: {work}"
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

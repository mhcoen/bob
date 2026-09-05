"""Installed-artifact probe, copied outside the checkout by smoke_wheels.py."""

from __future__ import annotations

import argparse
import importlib
import json
import runpy
import sys
from pathlib import Path
from unittest.mock import patch


def forbid_external_calls(event: str, args: tuple) -> None:
    if event in {"subprocess.Popen", "socket.connect", "os.system", "os.posix_spawn"}:
        raise RuntimeError(
            f"Wheel smoke must not invoke external processes or services: {event}"
        )


def check_runtime() -> None:
    prefix = Path(sys.prefix).resolve()
    for name in ("bob_tools", "orchestra", "mcloop", "duplo"):
        module = importlib.import_module(name)
        origin = Path(module.__file__).resolve()
        assert origin.is_relative_to(prefix), (
            f"{name} imported from outside the venv: {origin}"
        )
        print(f"{name}: {origin}")

    check_installers()

    from duplo.platforms.resolver import resolve_profiles
    from duplo.questioner import BuildPreferences

    for language in ("Python", "Swift"):
        profiles = resolve_profiles(
            BuildPreferences(platform="macOS", language=language)
        )
        assert profiles, f"No installed profile matched {language} on macOS"
        print(f"{language} profiles: {[profile.id for profile in profiles]}")

    from orchestra.api.registry import _pre_load_registry
    from orchestra.loader import load_workflow
    from orchestra.schema import load_schema

    for name in ("orchestra", "duplo"):
        package = Path(importlib.import_module(name).__file__).parent
        workflow_dir = package / "workflows"
        paths = sorted(workflow_dir.glob("*.orc"))
        assert paths, f"No bundled workflows in {package}"
        for path in paths:
            # Duplo's caller-owned transforms are registered at runtime; parsing
            # its assets still permits verification of all resource references.
            from orchestra.loader.parser import parse_workflow

            workflow = parse_workflow(path.read_text(), path)
            for role in workflow.roles:
                prompt = role.default_prompt
                if prompt.path:
                    assert (path.parent / prompt.path).is_file(), (path, prompt.path)
            for artifact in workflow.artifacts:
                if artifact.schema_path:
                    schema_path = path.parent / artifact.schema_path
                    load_schema(schema_path)
            if name == "orchestra":
                load_workflow(path, _pre_load_registry())
        print(f"{name}: {len(paths)} bundled workflows and their resources checked")

    from orchestra.executor.executor import Executor, new_run_id
    from orchestra.log import LogReader, LogWriter
    from orchestra.registry.registry import with_core
    from orchestra.spine import NO_INITIAL
    from orchestra.store import ArtifactStore

    registry = with_core()  # Only deterministic mock actor backings.
    package = Path(importlib.import_module("orchestra").__file__).parent
    workflow_path = package / "workflows" / "ask_single.orc"
    workflow = load_workflow(workflow_path, registry)
    run_dir = Path.cwd() / "mock-run"
    run_dir.mkdir()
    run_id = new_run_id()
    store = ArtifactStore(run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    try:
        for artifact in workflow.artifacts:
            qualifiers = (
                {} if artifact.initial is NO_INITIAL else {"initial": artifact.initial}
            )
            store.declare(artifact.name, artifact.type, qualifiers=qualifiers)
        log.write("run_start", fields={"workflow_path": str(workflow_path)})
        executor = Executor(
            workflow=workflow,
            registry=registry,
            store=store,
            log=log,
            run_dir=run_dir,
            run_id=run_id,
            external_inputs={"query": "wheel smoke", "history": ""},
        )
        terminal = executor.run_to_completion()
        log.write("run_end", fields={"terminal": terminal})
        assert terminal == "done", terminal
        output = store.read_latest("responder_output")
        assert output is not None and output.value.startswith("[mock-llm response to:")
    finally:
        log.close()
        store.close()
    records = LogReader(run_dir / "log.jsonl").read_all()
    assert any(record.event == "artifact_write" for record in records)
    print("ask_single: mock workflow completed with a durable artifact and log")

    # Catch transitive editable imports too, not just the four top-level modules.
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] in {"bob_tools", "orchestra", "mcloop", "duplo"}:
            origin = getattr(module, "__file__", None)
            if origin:
                assert Path(origin).resolve().is_relative_to(prefix), (name, origin)
    print(json.dumps({"runtime": "passed", "external_calls": "forbidden"}))


def check_installers() -> None:
    """Exercise resource consumers without running setup's provider probes."""
    from bob_tools.bob_cli import main as bob_main
    from mcloop.install_cmd import (
        _HOOK_SCRIPTS,
        _install_hooks,
        _install_recommended_permissions,
        _merge_settings,
        check_hook_drift,
    )

    home = Path.home()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    unrelated = {"permissions": {"allow": ["Read"]}, "custom": "preserved"}
    settings.write_text(json.dumps(unrelated))
    assert bob_main(["install"]) == 0
    first = settings.read_bytes()
    assert bob_main(["install"]) == 0
    assert settings.read_bytes() == first
    bob_hook = home / ".claude" / "hooks" / "telegram-permission-hook.py"
    assert bob_hook.is_file()

    assert all(status == "installed" for _, status in _install_hooks())
    _merge_settings()
    first = settings.read_bytes()
    _merge_settings()
    assert settings.read_bytes() == first
    assert check_hook_drift() == []
    hooks_dir = home / ".mcloop" / "hooks"
    for name in _HOOK_SCRIPTS:
        compile((hooks_dir / name).read_text(), name, "exec")
    assert (hooks_dir / bob_hook.name).read_bytes() == bob_hook.read_bytes()
    (hooks_dir / bob_hook.name).write_text("# stale installed hook\n")
    assert check_hook_drift() == [bob_hook.name]
    assert any(status == "updated (was stale)" for _, status in _install_hooks())
    assert check_hook_drift() == []
    assert (hooks_dir / bob_hook.name).read_bytes() == bob_hook.read_bytes()

    _, result = _install_recommended_permissions()
    assert result == "installed, merge manually", result
    recommended = json.loads(
        (home / ".mcloop" / "recommended-permissions.json").read_text()
    )
    from mcloop import install_cmd

    source = Path(install_cmd.__file__).parent / "resources" / "settings.example.json"
    expected = json.loads(source.read_text()).get("permissions", {}).get("allow", [])
    assert recommended == {"permissions": {"allow": expected}}
    current = json.loads(settings.read_text())
    assert all(current[key] == value for key, value in unrelated.items())
    commands = [
        hook["command"]
        for entry in current["hooks"]["PreToolUse"]
        for hook in entry.get("hooks", [entry])
    ]
    assert sum("telegram-permission-hook.py" in cmd for cmd in commands) == 1
    print(
        "installers: shared hooks, repeat installation, drift repair, permissions preserved"
    )

    scripts = Path(sys.prefix) / "bin"
    for name in ("appshot", "cgwindowid.swift", "mcloop-audit"):
        assert (scripts / name).is_file(), f"Missing installed script: {name}"
    assert not (scripts / "cgwindowid").exists(), (
        "Wheel must not ship a host-built binary"
    )
    # The audit command is standalone Python and should work on an empty log dir.
    logs = Path.cwd() / "audit-logs"
    logs.mkdir()
    with patch.object(sys, "argv", [str(scripts / "mcloop-audit"), str(logs)]):
        try:
            runpy.run_path(str(scripts / "mcloop-audit"), run_name="__main__")
        except SystemExit as exc:
            assert exc.code in (None, 0), exc.code
    print("helpers: portable Swift source and script launchers installed; audit runs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-home", required=True, type=Path)
    parser.add_argument("--cli")
    args = parser.parse_args()
    sys.addaudithook(forbid_external_calls)
    with patch.object(Path, "home", return_value=args.sandbox_home):
        if args.cli:
            launcher = Path(sys.prefix) / "bin" / args.cli
            sys.argv = [str(launcher), "--help"]
            runpy.run_path(str(launcher), run_name="__main__")
        else:
            check_runtime()


if __name__ == "__main__":
    main()

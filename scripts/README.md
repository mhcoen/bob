# Installed-wheel smoke test

From the workspace root, with Python 3.12+ and `uv` on PATH:

```bash
python3 scripts/smoke_wheels.py
```

The command creates a temporary directory outside the checkout and preserves it
for inspection. Use `--work-dir /tmp/bob-wheel-check` to choose a new directory;
an existing directory is refused. Once dependencies are cached, use `--offline`
to prohibit downloads during provisioning. A cache miss fails visibly.

The harness builds all four packages from fresh copies, excluding build outputs,
editable metadata, caches, and logs. It compares wheel members against an
independent inventory of runtime Python modules and workflow resources, then
installs the wheels and their declared dependencies in a new virtual environment.
Dependency provisioning may use the package index. It does not change the
workspace environment or install development extras.

Runtime checks run from a separate project directory with Python isolated mode,
no inherited provider credentials or Python path overrides, and a temporary
`Path.home()`. An audit hook rejects subprocess launches and network connections
inside each probe. Installed Python CLI launchers are exercised with `--help`
through isolated Python processes; no provider CLI is needed.

The CI wheel jobs also run the separate
[verified-change example](../examples/verified-change/README.md) with the installed
wheel interpreter. That example executes real Git and candidate Python subprocesses
and retains acceptance and interruption evidence in its own seven-day artifact.
It does not run inside the wheel probe's subprocess-denying audit hook.

The smoke verifies:

- `bob`, `bob-plan`, `orchestra`, `mcloop`, and `duplo` launcher startup;
- import origins for all four packages and their imported submodules;
- installed Python and Swift platform profile resolution;
- Bob and McLoop hook installation in a temporary home, repeat installation,
  stale-hook detection and repair, recommended permissions, and preservation of
  unrelated settings;
- installed `appshot`, portable `cgwindowid.swift`, and `mcloop-audit` scripts,
  including execution of the Python audit utility against an empty log directory;
- prompt and schema references in every bundled Duplo and Orchestra workflow,
  plus Orchestra workflow validation;
- execution of bundled `ask_single` with deterministic mock actors, including
  its persisted result and event log.

Inspect `build.log`, `install.log`, `cli-*.log`, `runtime.log`, and `result.json`
in the printed artifact directory. `result.json` is written after the runtime
checks; a provisioning failure exits earlier with its failed step's log.
`installed-requirements.txt` records the actual installed versions. Dependencies
are resolved from wheel metadata rather than pinned to the workspace lockfile;
this checks distribution metadata as consumed by a new installation. Retain the
artifact directory when comparing runs.

This is a separate packaging gate, not part of ordinary pytest collection. It
does not run real models or capture a macOS window. It exercises the installer
resource consumers; the full interactive `mcloop install` also probes the provider
CLI and prompts for configuration, which this smoke does not invoke.

## Continuous integration

[packaging.yml](../.github/workflows/packaging.yml) runs on pushes, pull requests,
and manual dispatch. Wheels are tested on Linux and macOS with Python 3.12 and
3.13. A separate macOS/Python 3.13 job installs ffmpeg, syncs all locked workspace
dev dependencies, runs lint, and activates the virtual environment before running
the workspace tests so subprocesses can find tools such as ruff. The video tests
mock ffmpeg availability for unit tests; the real extraction test skips locally
when ffmpeg is absent. CI checks that ffmpeg is on PATH before running tests.
Default integration-test skips remain
in effect; no provider credentials are configured. Wheel evidence is retained
for seven days, including failed-step logs.

Action revisions are pinned. The setup uses the documented
[setup-uv inputs](https://github.com/astral-sh/setup-uv/blob/v7/action.yml) and
[artifact upload inputs](https://github.com/actions/upload-artifact/blob/v7/action.yml).
Hosted execution still needs a push; a local smoke result is not a GitHub Actions
run result.

## Runtime asset inventory and remaining gaps

Inventory taken for improvement-plan Slice A, 2026-09-05:

| Asset | Consumer and distribution status |
| --- | --- |
| Duplo platform Python modules | Discovered recursively and verified in the wheel and installed profile resolver. |
| Duplo `.orc`, prompt templates, and schemas | Bundled package data; verified by the smoke. |
| Orchestra `.orc`, prompt templates, schemas, and `py.typed` | Bundled package data; verified by the smoke. |
| Duplo starter-spec template | Embedded in `duplo/spec_writer.py`; no runtime dependency on the repository's `SPEC-template.md`. |
| McLoop `bin/appshot` | Declared as an installed script; actual screenshot behavior requires macOS and is outside this smoke. |
| McLoop hook scripts and `settings.example.json` | Canonical files live in `mcloop/resources/` and ship as package data. Original checkout paths are symlinks. Installers and drift checks read the package resources. The current settings example has no permissions block, so its recommended allow list is empty. |
| Bob's default hook lookup | Finds McLoop's top-level module location without importing its module graph, then reads the shared resource. `--hook` remains usable when bob-tools is installed alone. |
| McLoop `bin/cgwindowid.swift` and `bin/mcloop-audit` | Installed beside `appshot`. The portable Swift source can be compiled by appshot; the host-built `bin/cgwindowid` binary is deliberately excluded from wheels. Native compilation and screenshots are not exercised by this smoke. |

There is one canonical copy of each hook source. Both installers consume it and
retain their existing configuration destinations. Actual screenshots still need
macOS permissions and Swift tooling; shipping the helper source does not establish
that native capture works on a particular host.

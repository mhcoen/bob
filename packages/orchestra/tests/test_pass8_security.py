"""Pass-8 security regression tests.

The audit's pass-8 finding was three security-class leaks of the
same shape (sensitive content persisted on disk in surfaces the
pass-7 bounded fix did not cover):

#1 Mock adapters still placed prompt[:160] in prepared.summary,
   which the executor persists as part of actor_prepare.
#2 Prompt snapshots were created under default umask 022, leaving
   the snapshot directory world-readable and snapshot files
   world-readable.
#3 REPL history at ~/.orchestra/history was world-readable for the
   same reason.

These tests reproduce the leaks the audit demonstrated (with a
sentinel ``SECRET_TOKEN_123`` payload where applicable) and assert
they are closed post-fix.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestra.adapters.mock_human import MockHumanAdapter
from orchestra.adapters.mock_model import MockModelAdapter
from orchestra.prompt_snapshot import snapshot_prompt_sources
from orchestra.spine import (
    ActorBinding,
    InvocationRequest,
    PromptSource,
    RoleDecl,
    StateDecl,
    Transition,
    Workflow,
    WriteDecl,
)

# --------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------


@contextlib.contextmanager
def _forced_umask(value: int) -> Iterator[None]:
    """Force a specific umask for the duration of the block.

    Pass-8 leaks all manifest under the default 022 umask. Tests run
    in environments where the developer's umask may already be
    tightened (e.g. 077) which would mask the leak. Forcing 022 inside
    the test ensures the regression assertion is meaningful.
    """
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


def _request_with_prompt(
    prompt: str,
    *,
    options: tuple[str, ...] = (),
) -> InvocationRequest:
    backing_options: dict[str, object] = {}
    if options:
        backing_options["options"] = list(options)
    return InvocationRequest(
        state_id="s",
        attempt=1,
        actor_binding={"kind": "model"},
        reads={},
        external_inputs={},
        prompt_artifact=prompt,
        schema=None,
        backing_options=backing_options,
        timeout_ms=None,
    )


# --------------------------------------------------------------------
# #1 Mock adapter prepared.summary
# --------------------------------------------------------------------


def test_mock_model_adapter_summary_does_not_carry_prompt_body() -> None:
    """Pass-8 fix #1: MockModelAdapter.prepare must not place the
    prompt body in the durable summary. The audit verified the leak
    by passing SECRET_TOKEN_123; post-fix, only a sha256 digest
    remains."""
    adapter = MockModelAdapter()
    secret_prompt = "SECRET_TOKEN_123 prompt content"
    prepared = adapter.prepare(_request_with_prompt(secret_prompt))
    summary = prepared.summary
    assert "SECRET_TOKEN_123" not in str(summary)
    assert "prompt_preview" not in summary
    expected_digest = hashlib.sha256(
        secret_prompt.encode("utf-8")
    ).hexdigest()
    assert summary["prompt_sha256"] == expected_digest
    assert summary["prompt_chars"] == len(secret_prompt)


def test_mock_human_adapter_summary_does_not_carry_prompt_body() -> None:
    adapter = MockHumanAdapter()
    secret_prompt = "SECRET_TOKEN_123 in human gate prompt"
    prepared = adapter.prepare(
        _request_with_prompt(
            secret_prompt, options=("accept", "reject")
        )
    )
    summary = prepared.summary
    assert "SECRET_TOKEN_123" not in str(summary)
    assert "prompt_preview" not in summary
    expected_digest = hashlib.sha256(
        secret_prompt.encode("utf-8")
    ).hexdigest()
    assert summary["prompt_sha256"] == expected_digest
    assert summary["prompt_chars"] == len(secret_prompt)


# --------------------------------------------------------------------
# #2 Snapshot directory and file modes
# --------------------------------------------------------------------


def _build_workflow_with_secret_prompt(tmp_path: Path) -> Workflow:
    tmpl = tmp_path / "templates"
    tmpl.mkdir(parents=True, exist_ok=True)
    (tmpl / "secret.md").write_text("SECRET_TOKEN_123 in template body\n")
    return Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(
                    kind="template",
                    path="templates/secret.md",
                    template_vars=(),
                ),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )


def test_snapshot_directory_is_private_under_default_umask(
    tmp_path: Path,
) -> None:
    """Pass-8 fix #2: under default umask 022, the snapshot directory
    must still come out 0700. Without this the directory is 0755 and
    other local users can enumerate it. The test forces the umask
    inside the call so the regression assertion is meaningful even
    when the developer's environment runs a tighter umask."""
    workflow = _build_workflow_with_secret_prompt(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with _forced_umask(0o022):
        _, _ = snapshot_prompt_sources(workflow, run_dir)
    snapshot_dir = run_dir / "prompt_sources"
    assert snapshot_dir.is_dir()
    mode = stat.S_IMODE(snapshot_dir.stat().st_mode)
    assert mode == 0o700, (
        f"snapshot directory mode is {oct(mode)}, expected 0o700"
    )


def test_snapshot_files_are_private_under_default_umask(
    tmp_path: Path,
) -> None:
    """Pass-8 fix #2: snapshot files must be written 0600 even when
    the calling process's umask is 022."""
    workflow = _build_workflow_with_secret_prompt(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with _forced_umask(0o022):
        _, manifest = snapshot_prompt_sources(workflow, run_dir)
    assert len(manifest) == 1
    snapshot_path = Path(manifest[0]["snapshot_path"])
    assert snapshot_path.is_file()
    body = snapshot_path.read_text(encoding="utf-8")
    assert "SECRET_TOKEN_123" in body, (
        "sanity check: the snapshot must actually carry the "
        "sensitive content the mode is protecting"
    )
    mode = stat.S_IMODE(snapshot_path.stat().st_mode)
    assert mode == 0o600, (
        f"snapshot file mode is {oct(mode)}, expected 0o600"
    )


# --------------------------------------------------------------------
# #3 REPL history file mode
# --------------------------------------------------------------------


def test_repl_history_file_is_private_under_default_umask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass-8 fix #3: ~/.orchestra/history must be 0600 and its
    parent ~/.orchestra must be 0700. The audit verified the file
    ends up 0644 by default, exposing prompt_toolkit's persisted
    query lines (which can carry secrets) to other local users.

    History stays enabled per the audit decision; only the file
    permission tightens.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    # Re-import the repl module's _HISTORY_PATH after HOME swap; it's
    # a module-level constant, so patch it directly.
    from orchestra import repl as repl_mod
    history_path = fake_home / ".orchestra" / "history"
    monkeypatch.setattr(repl_mod, "_HISTORY_PATH", history_path)

    with _forced_umask(0o022):
        repl_mod._build_session()

    parent_mode = stat.S_IMODE(history_path.parent.stat().st_mode)
    file_mode = stat.S_IMODE(history_path.stat().st_mode)
    assert parent_mode == 0o700, (
        f"~/.orchestra mode is {oct(parent_mode)}, expected 0o700"
    )
    assert file_mode == 0o600, (
        f"history file mode is {oct(file_mode)}, expected 0o600"
    )


# --------------------------------------------------------------------
# Pass-9 fix: subprocess transcript modes
# --------------------------------------------------------------------


def test_write_log_transcript_is_private_under_default_umask(
    tmp_path: Path,
) -> None:
    """Pass-9 fix: subprocess transcript logs duplicate raw model
    stdout/stderr at project_dir/.mcloop/logs/<timestamp>.log. Under
    default umask 022 the directory is 0755 and the file 0644,
    leaving any secret, customer snippet, internal doc excerpt, or
    tool output the model printed readable by other local users.
    Force umask 022 so the assertion is meaningful regardless of the
    developer's environment."""
    from orchestra.adapters import _subprocess

    log_dir = tmp_path / ".mcloop" / "logs"
    with _forced_umask(0o022):
        log_path = _subprocess.write_log(
            log_dir,
            "task label",
            ["echo", "hi"],
            "MODEL OUTPUT WITH SECRET_TOKEN_123 inside\n",
            0,
        )
    assert log_path.is_file()
    body = log_path.read_text(encoding="utf-8")
    assert "SECRET_TOKEN_123" in body, (
        "sanity check: the transcript must actually carry the "
        "sensitive content the mode is protecting"
    )
    file_mode = stat.S_IMODE(log_path.stat().st_mode)
    assert file_mode == 0o600, (
        f"transcript file mode is {oct(file_mode)}, expected 0o600"
    )
    log_dir_mode = stat.S_IMODE(log_dir.stat().st_mode)
    assert log_dir_mode == 0o700, (
        f"log dir mode is {oct(log_dir_mode)}, expected 0o700"
    )
    mcloop_mode = stat.S_IMODE(log_dir.parent.stat().st_mode)
    assert mcloop_mode == 0o700, (
        f".mcloop mode is {oct(mcloop_mode)}, expected 0o700"
    )


def test_write_log_tightens_existing_loose_mcloop_directory(
    tmp_path: Path,
) -> None:
    """If a prior mcloop run left .mcloop or .mcloop/logs at 0755,
    the next write_log must chmod them down. Regression coverage for
    the 'pre-existing directory keeps its old mode' edge case the
    pass-8 fix shipped on the run directory; the same shape applies
    here."""
    from orchestra.adapters import _subprocess

    log_dir = tmp_path / ".mcloop" / "logs"
    log_dir.mkdir(parents=True, mode=0o755)
    log_dir.chmod(0o755)
    log_dir.parent.chmod(0o755)
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE(log_dir.parent.stat().st_mode) == 0o755

    with _forced_umask(0o022):
        log_path = _subprocess.write_log(
            log_dir,
            "task",
            ["echo"],
            "body\n",
            0,
        )

    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_dir.parent.stat().st_mode) == 0o700

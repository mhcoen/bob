# One independently verified change

This example fixes a tiny Python CLI that undercounts an inclusive integer range.
It demonstrates why project tests alone are insufficient and preserves evidence
connecting a requirement, task, checked candidate, commit, and ledger event.

From the Bob checkout, after `uv sync --locked --all-packages --all-extras`:

```bash
.venv/bin/python examples/verified-change/run.py demo /tmp/bob-span-demo
```

Python 3.12+ and Git are required. The output directory must be new. The example
uses temporary Git repositories with no remotes, disables Git hooks and global
Git configuration, and passes no inherited provider credentials to acceptance
processes. It preserves artifacts and makes no model calls by default.

The demonstration uses the real planfile, McLoop ownership/completion receipt,
acceptance-command, and ledger APIs. Its small reference orchestrator controls
the lifecycle; it does not invoke McLoop's full scheduler or Duplo discovery.
See the [design contract](../../design/verified-example.md) for that boundary.

## Read the result

`result.json` links to one `acceptance.json` per scenario:

| Candidate | Existing project tests | Independent acceptance |
| --- | --- | --- |
| Correct endpoint fix | Pass | Accept |
| Plausible fix mishandling negative endpoints | Pass | Reject |
| Removed project assertions | Pass | Reject |
| No edit | Pass | Reject |
| README-only edit | Pass | Reject |
| Correct fix, interrupted after commit, then reconciled | Pass | Accept |

Requirement `REQ-SPAN-001` version 1 says `span.py START END` prints the number of
integers including both endpoints, or zero for a reversed range. The external
oracle runs explicit positive, negative, singleton, crossing-zero, and reversed
cases. These cases establish the stated finite acceptance coverage; they do not
prove correctness for every possible integer or measure model competence.

Each acceptance record contains baseline and candidate commit IDs, the staged Git
tree ID, requirement/task IDs, oracle version and SHA-256, checked file hashes,
commands, exit codes, output, log paths, receipt path, and ledger event IDs.
Rejected candidates retain their tree and snapshot but have no implementation
commit and leave the task unchecked. Unknown token and cost usage is `null`.

Inspect `wrong/checks.log` for the failed negative-range cases. Inspect
`correct/project/PLAN.md` for the completed task, the referenced completion receipt
under `correct/project/.mcloop/completions/`, and the ledger under `.duplo/ledger/`.
The plan completion is deliberately a working-tree update after the implementation
commit, matching the receipt ordering. `candidate_commit` identifies the checked
implementation, whose plan still shows the pending task. The demo does not make
a second checkpoint commit for that plan update.

Run one scenario separately (rejection exits 1):

```bash
.venv/bin/python examples/verified-change/run.py run /tmp/bob-span-wrong --scenario wrong
```

## Interruption and reconciliation

The demo's worker exits immediately after Git commits the correct candidate,
before the completion receipt records the returned commit ID. Ordinary restart
refuses to edit because the execution and completion receipts remain unresolved.
`interrupted/recovery-before.json` captures that evidence.

The example then explicitly reconciles this one boundary: it requires unchanged
project files, the original plan, the expected commit parent and tree, and an
unchanged oracle. It repeats the independent checks, settles the completion and
ledger event, and acknowledges the abandoned execution receipt. Resume must see
the completed task without another editor invocation or implementation commit.

To examine the boundary manually, create the fixture and interrupt its worker:

```bash
.venv/bin/python examples/verified-change/run.py interrupt /tmp/bob-span-interrupted
# Exit 71 is intentional. Inspect the project and acceptance.json before continuing.
.venv/bin/python examples/verified-change/run.py recover /tmp/bob-span-interrupted
```

Changed evidence is refused and retained. The helper supports only this documented
post-commit/pre-return boundary; interruption during reconciliation still requires
operator inspection using the [persistence procedures](../../design/persistence.md).
It is not automatic recovery for arbitrary McLoop runs.

## Optional model-backed editor

Use an adapter executable for your configured model. The harness calls it once,
with a 120-second timeout, in a temporary working directory:

```bash
.venv/bin/python examples/verified-change/run.py run /tmp/bob-span-live \
  --editor-command /absolute/path/to/your-model-adapter
```

The adapter reads one JSON object from stdin containing `requirement_id`,
`requirement`, and `files`. It must write only a JSON object mapping any of
`span.py`, `regression.py`, and `README.md` to their complete replacement text.
An empty object means no edit. Other paths and non-string contents are rejected
before any returned file is written. The adapter receives no oracle or project
path. The same external acceptance gate judges its returned files.

The explicitly invoked adapter inherits your environment so it can use configured
authentication. Choose a trusted adapter; its internal retries, model choice, and
cost controls are its responsibility. The harness bounds the command invocation,
not token usage or spending. No particular provider adapter is bundled or tested
against a live service. The protocol is tested with a deterministic executable.

The oracle is outside the candidate's returned-file interface, and checks run
against an exported staged tree with before/after hashes. This is not an operating
system sandbox or a signed attestation: candidate Python code and a trusted editor
still execute under your account. The example targets incorrect candidates and
weakened tests, not hostile code attempting to escape the process.

## CI

The four Linux/macOS, Python 3.12/3.13 wheel jobs run the six-scenario demo using
freshly installed wheels. Each preserves the entire generated evidence directory
for seven days. Workspace regressions additionally check changed oracle/project/
commit refusal, the external editor's path restriction, and output preservation.

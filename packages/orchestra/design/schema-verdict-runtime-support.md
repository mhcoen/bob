# Schema Verdict Runtime Support

Date: 2026-05-04
Status: Design draft for review
Predecessor: design/iteration-and-implementation-workflows.md (pass-1
audit identified schema-verdict wiring as a missing runtime feature)

## What this commit adds

The runtime mechanism for schema-backed verdict routing on
model/agent states. Before this commit, a state can write a json
artifact but the runtime cannot:

1. Parse the model's text output as JSON,
2. Validate it against a declared schema,
3. Populate the artifact with the parsed object,
4. Derive the transition outcome from a named field of the parsed
   object.

After this commit, all four are first-class runtime capabilities,
exposed through a new `schema` clause on state declarations and a
new artifact qualifier referencing a JSON Schema file. This is the
foundation the `iterate_until_acceptable` and
`propose_review_judge_implement` workflows need.

## Non-goals

- The two consuming workflows. Those land in a separate commit.
- General-purpose JSON Schema dialect support beyond what the
  enum-routing decision field requires. v0 supports a minimal
  schema shape; full Draft 2020-12 is out of scope.
- Schema versioning, migration, or dialect negotiation. Schemas
  are external files referenced by path.
- Adapter-side schema enforcement (asking the model to produce
  schema-conformant output via tool use, structured output APIs,
  or grammar-constrained sampling). v0 validates after the fact;
  schema-driven prompting is a v1 question.

## Design

### Surface: artifact and schema declarations

Schemas are declared as a new artifact qualifier:

```
artifact judge_verdict json
  schema "schemas/judge_verdict.json"
```

The `schema` qualifier names a JSON Schema file relative to the
.orc workflow's source directory. The file is loaded at workflow
load time, parsed, and validated against the supported schema
shape (see "Supported schema shape" below). A schema whose shape
is not supported is a load error.

The grammar already admits a `schema <identifier>` clause on
states (per `orchestra-grammar.md`). We deprecate that
state-level form and replace it with the artifact-level
qualifier above. State-level `schema` is removed from the
grammar in this commit; the validator rejects it with a clear
error pointing at the new form. (Rationale: a schema describes
the shape of an artifact, not the shape of a state. Putting it
on the artifact declaration matches how `initial`, `source`, and
type qualifiers already work.)

### Surface: state writes and outcomes

A state that writes a schema-backed json artifact gains
schema-derived outcomes:

```
state judge
  actor model m_judge
  role judge
  reads query, proposal, review_output
  writes judge_verdict json
  on accept => done
  on iterate when attempts.judge < 6 => review
  on iterate => done
  on error => stop
  on timeout => stop
```

The transition outcomes `accept` and `iterate` are derived from
the `decision` field of the schema-backed `judge_verdict`
artifact. The outcomes are not free-form; they must exactly
match the enum values declared in the schema (see "Outcome
derivation" below).

The `complete` outcome is replaced by the schema enum outcomes on
schema-backed states. A schema-backed state that declares
`on complete` is a load error.

### Surface: field extraction

A schema-backed artifact may extract one or more of its fields into
separately declared text artifacts. The clause is a repeatable
third qualifier on the artifact declaration:

```
artifact judge_verdict json
  schema "schemas/iterate_judge_verdict.json"
  extract feedback => judge_feedback text
  extract fix_instructions => fix_instructions text
```

Each `extract` clause has three pieces:

- The source field name (`feedback`). Must be a top-level property
  of the schema. Compound paths such as `nested.subfield` are not
  supported in v0.
- The target artifact name (`judge_feedback`). Must be a separately
  declared artifact in the workflow.
- The target type. Must be `text` in v0. A future extension may
  permit `json` for nested-object extraction.

The source field's schema type must be one of `string`, `integer`,
`number`, or `boolean`. v0 permits `string` schema fields to
extract into `text` artifacts directly. Numeric and boolean
fields are converted to their canonical text form. Object or
array source fields are a load error in v0.

Behavior at runtime: after a state writes the schema-backed JSON
artifact, the executor performs each declared extraction in the
same transaction as the JSON write.

1. Look up the source field in the validated object.
2. If present, convert its value to text and write the target
   artifact.
3. If absent (optional field omitted from the parsed object), the
   target artifact is unchanged. Its prior value (for the first
   write, the `initial` value declared on the target artifact) is
   retained.

The state declaring a schema-backed artifact's write must also
list each extraction target in its `writes` clause. The validator
rejects a state that writes the JSON artifact but does not list
the extraction targets. This keeps the data-flow visible at the
state site without requiring the validator to compute implicit
writes.

Extraction failures (target type mismatch, missing target
artifact, source field absent from the schema) are load errors,
not runtime errors. By the time the workflow runs, every
extraction is known to be well-typed.

Additionally, the validator requires the source field of any
`extract` clause whose target artifact is read by any state in
the workflow to be in the schema's `required` list. This
prevents a state from reading stale extracted data left over
from a prior iteration when the source field is omitted from a
valid model output. Extractions whose targets are unread by
downstream states are unaffected by this rule and may freely
reference optional schema fields.

### Supported schema shape

v0 supports JSON Schemas of the following shape only:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["decision"],
  "properties": {
    "decision": {
      "type": "string",
      "enum": ["accept", "iterate"]
    },
    "feedback": { "type": "string" }
  },
  "additionalProperties": false
}
```

Concretely:

- The root must be a JSON object (`type: object`).
- The `decision` field must be in the schema's `required` list,
  of type string, with an `enum` constraint listing the allowed
  values. The enum values become the state's transition outcomes.
- The `required` list may contain additional fields beyond
  `decision`. Fields in `required` must be present in any
  validated object. Fields not in `required` are optional. All
  fields are validated against their declared types. Supported
  field types: `string`, `integer`, `number`, `boolean`, `array`
  (of supported types), `object` (with the same shape
  constraints).
- Extraction targets read by any state in the workflow force
  their source fields into the `required` list per the rule in
  "Surface: field extraction" above. A schema that lists such a
  field only under `properties` and not under `required` is
  rejected at workflow load time.
- `additionalProperties: false` is recommended but not required.

A schema that does not have a `decision` enum field is a load
error: such a schema cannot drive transitions. (A future
extension may allow the routing field to be configurable via
e.g. `routes_on: "verdict"`; v0 fixes the field name as
`decision`.)

### Outcome derivation

When a schema-backed state writes its json artifact, the runtime:

1. Parses the model's text output as JSON. Parse failure is the
   `error` outcome. The error envelope records the parse failure
   message and the raw output (subject to the same redaction
   rules pass-7/pass-8 applied to other persisted content).

   Schema-backed model outputs are parsed with a tolerant
   JSON-object extraction step before schema validation. The
   extractor scans the raw model text for balanced top-level
   `{...}` spans, respecting JSON string and escape boundaries,
   then attempts `json.loads` from the last span to the first and
   returns the first span that parses. The extractor is
   schema-agnostic: once it returns a parsed object, schema
   validation is final and the runtime does not retry earlier JSON
   candidates after a schema violation. If no balanced object is
   found, or no candidate parses cleanly, the state records a
   schema parse_error.

2. Validates the parsed object against the declared schema. Any
   schema violation (missing required field, wrong type, enum
   miss on `decision`, additional property when forbidden) is the
   `error` outcome.

3. Populates the json artifact with the parsed and validated
   object.

4. Reads the value of `decision` and emits that string as the
   state's transition outcome.

The runtime rejects the workflow at load time if the .orc's set
of declared transition outcomes does not exactly match the
schema's `decision` enum (plus `error` and `timeout`). Specifically:

- Every enum value must have a corresponding `on <enum_value>`
  transition.
- Every `on <outcome>` transition (other than `error` / `timeout`
  / outcomes from non-schema states) must correspond to an enum
  value.

Mismatch is a load error.

### Adapter contract changes

Adapters do not need to change for this commit. The schema layer
sits between the adapter's text output and the artifact write.
Adapters continue to return text; the runtime parses, validates,
and routes.

A future extension may add a `schema_hint` to the adapter prompt
(asking the model to emit JSON conforming to the schema) but v0
relies on the prompt template carrying the schema shape
explicitly. Workflow authors are responsible for prompts that
produce schema-conformant output.

### Logging and resume

The schema validation step produces a log record:

```json
{
  "type": "schema_validation",
  "state": "judge",
  "attempt": 1,
  "outcome": "valid" | "parse_error" | "schema_error",
  "decision": "accept" | "iterate" | null,
  "validation_errors": [...],
  "raw_output_ref": "<payload_ref>"
}
```

The raw output is stored as a payload (subject to the existing
prompt-snapshot security discipline: 0600 mode, in the run
directory). On resume, the payload is read back and re-validated
to confirm the recorded outcome is reproducible. A resume that
sees a different validation result (because the schema file
changed between crash and resume, for example) refuses with a
clear error.

This means the schema file itself becomes part of the prompt
manifest (already snapshotted by the pass-5 redesign). The
manifest extension is small: schema files are added to the same
walk that finds prompt sources.

### Error handling

Three new error classes:

1. **Parse error**: model output is not valid JSON. The state's
   `error` outcome fires. The error envelope's `reason` field is
   `"json_parse"` with the parse error message.

2. **Schema error**: parsed JSON does not match the schema. The
   state's `error` outcome fires. The error envelope's `reason`
   field is `"schema_violation"` with the validator's error list.

3. **Outcome mismatch at load time**: the workflow's transition
   set doesn't match the schema's enum. Load error, never reaches
   runtime.

The first two are `error`-class outcomes, so the existing
`on error` and `retry max N then ...` mechanisms apply
unchanged. A workflow author who expects schema violations to
be retryable can declare `on error retry max 2 then stop` on a
schema-backed state, and the model will be re-invoked twice
before the workflow gives up.

### Schema artifact and the prompt-source snapshot

Schema files are static inputs. They are snapshotted into the
run directory at run_start, identically to prompt files. The
existing `prompt_snapshot` module gains schema files as a second
class of file-backed input it walks.

The walk is extended to include any artifact's `schema "..."`
qualifier in addition to the prompt sources it already covers.
Resume verifies the schema file's hash matches the snapshot's
hash; mismatch refuses resume, identical to the existing prompt
file drift check.

## Implementation pieces

The commit comprises six pieces, in dependency order:

### 1. Grammar and IR

- Lexer/parser admits `schema "<path>"` as an artifact qualifier.
- Lexer/parser admits `extract <field> => <artifact> <type>` as a
  repeatable artifact qualifier on schema-backed artifacts.
- Lexer/parser removes state-level `schema <identifier>` (load
  error pointing at the new form).
- IR `ArtifactDecl` gains a `schema_path: str | None` field.
- IR `ArtifactDecl` gains `extractions: list[ExtractionDecl]`
  where `ExtractionDecl` is `(source_field: str, target: str, type: str)`.
- IR `StateDecl` no longer has a schema field.

### 2. Schema loader and validator

- New module `orchestra/schema.py`:
  - `load_schema(path: Path) -> SchemaSpec`: reads, parses, and
    validates a schema file against the supported shape.
    Returns a typed `SchemaSpec` carrying the enum values for
    `decision` and the field type map.
  - `SchemaSpec.validate(value: dict) -> ValidationResult`:
    validates a parsed JSON object against the schema. Returns
    a discriminated result: `Valid(decision=str)` or
    `Invalid(errors=list[str])`.
- Implementation: a small handwritten validator covering the
  supported shape. No external dependency.
- Tests cover: every error class, supported field types, the
  enum extraction, the `decision`-required check.

### 3. Validator integration

- Workflow validator extended:
  - Schema-backed json artifacts have their schema files loaded
    at workflow load time.
  - Schema-backed states' transitions are checked against the
    schema's enum, with the load error described above on
    mismatch.
  - Schema-backed states must not declare `on complete`.
  - The declared `writes` for the artifact must be `json`; a
    schema on a non-json artifact is a load error.
  - Each `extract` clause's source field must exist in the
    schema's properties and have a type in `{string, integer,
    number, boolean}`.
  - Each `extract` clause's target artifact must be separately
    declared in the workflow with type `text` in v0.
  - Every state that writes a schema-backed artifact must list
    each declared extraction target in its `writes` clause.
  - For each `extract` clause whose target artifact is read by
    any state in the workflow, the source field must appear in
    the schema's `required` list. This rule prevents a downstream
    state from reading stale extracted data when the source field
    is omitted from an otherwise valid model output. Extractions
    whose targets are unread by any state are exempt.

### 4. Executor integration

- After a schema-backed state's adapter returns:
  - Parse text output as JSON.
  - Validate against the schema.
  - On valid: populate the JSON artifact, perform any declared
    extractions atomically with the JSON write (look up each
    source field, convert to text, write the target artifact),
    emit the schema-derived outcome, log a `schema_validation`
    record with `outcome: valid`.
  - On parse error or schema error: emit the `error` outcome,
    log a `schema_validation` record with the error details, do
    not populate the artifact and do not perform extractions.
- The error envelope's `reason` field carries `"json_parse"` or
  `"schema_violation"` so downstream observers can distinguish.

### 5. Prompt-snapshot extension

- `prompt_snapshot.py`'s walk extended to include schema files.
- Manifest entries gain a `kind: "schema"` discriminator
  alongside the existing prompt-file kinds.
- Resume's verification covers schema files identically.
- File modes match: 0600 on snapshotted schema files.

### 6. Tests

- Unit tests on `schema.py` covering the supported shape.
- Integration tests on a minimal schema-backed workflow:
  - happy path (decision=accept routes to terminal),
  - alternate decision (decision=iterate routes to loop),
  - parse error (returns "error" outcome),
  - schema violation (returns "error" outcome),
  - outcome mismatch at load time,
  - schema file edited between crash and resume (refuses).
- Integration tests on extraction:
  - extract clause writes target artifact with the field value,
  - optional source field absent leaves target artifact unchanged
    (initial value retained on first write),
  - numeric and boolean source fields converted to canonical text,
  - validator rejects an extract clause whose source field is not
    in the schema, whose target artifact is undeclared, whose
    target type is not `text`, or whose source field has an
    unsupported schema type,
  - validator rejects a workflow whose extract target is read by
    a state but whose source field is not in the schema's
    `required` list.
- The minimal test workflow is a stripped Iterate without
  the actual reviewer/judge model calls — uses
  mock_model adapters returning canned JSON. This isolates
  the schema mechanism from the workflow design.

## Worked example

A minimal schema-backed workflow used as the integration test
fixture:

```
spec 0.1

workflow schema_smoke

  external_input query text

  max_total_steps 10

  model m_judge

  artifact verdict json
    schema "schemas/two_branch.json"
  artifact stub_artifact text initial ""

  role judge
    prompt template "templates/schema_smoke_judge.md" with query

  state judge
    actor model m_judge
    role judge
    reads query
    writes verdict json
    on accept => done
    on iterate => repeat
    on error => stop
    on timeout => stop

  state repeat
    actor model m_judge
    role judge
    reads query
    writes stub_artifact text
    on complete when attempts.repeat < 3 => judge
    on complete => done
    on error => stop
    on timeout => stop
```

Schema file `schemas/two_branch.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["decision"],
  "properties": {
    "decision": {
      "type": "string",
      "enum": ["accept", "iterate"]
    },
    "feedback": { "type": "string" }
  },
  "additionalProperties": false
}
```

The mock model adapter returns canned JSON per attempt, exercising
each schema branch and the error paths.

## Backward compatibility

- Workflows that don't use `schema` on artifacts are unaffected.
- The state-level `schema <identifier>` clause is removed. No
  shipped workflow uses it (verified: grep of
  `orchestra/workflows/`). The grammar's reserved-word list keeps
  `schema` reserved.
- Old runs in the JSONL log have no `schema_validation` records;
  resume handles their absence the same way it handles
  prompt_snapshot absence (legacy resume path).

## Open questions

1. **Schema file location convention.** The .orc references schema
   files by path relative to the workflow source directory. Should
   schemas live in `orchestra/workflows/schemas/` (a sibling of the
   workflows dir) or `orchestra/workflows/<workflow>/schema.json`
   (per-workflow)? The doc assumes `schemas/<name>.json` as a
   sibling directory, mirroring the `templates/` convention. Worth
   confirming.

2. **Whether `decision` should be configurable.** The spec fixes
   the routing field name as `decision`. A future workflow that
   wants to route on `verdict` or `outcome` would need to either
   rename to `decision` or extend the schema declaration syntax
   (e.g. `schema "..." routes_on "verdict"`). v0 fixes at
   `decision`; the extension is a v1 question.

3. **Adapter-side schema enforcement.** The spec validates after
   the fact. An alternative is to have adapters ask the model to
   produce schema-conformant output via structured-output APIs
   (Anthropic's tool use, OpenAI's response_format). v0 doesn't
   do this — the prompt template carries the responsibility. The
   trade-off is correctness (structured output is more reliable)
   vs. adapter complexity (each adapter needs schema-handling
   logic). Defer.

4. **Schema cross-references and `$ref`.** v0 supports inline
   schemas only. A schema that uses `$ref` to reference another
   schema or a JSON Schema definition is a load error. v1 may
   add this; v0 doesn't need it for the consuming workflows.

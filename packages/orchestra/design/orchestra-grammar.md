# Orchestra: Grammar

## What this document is

This is the follow-on to `orchestra-design.md` and
`orchestra-result-schemas.md` that pins down the concrete surface
syntax of Orchestra workflow files. It specifies the lexical
rules, the grammar in EBNF, the reserved word list, and how
grammar-level references resolve against the result envelope
defined in the result-schemas document.

The grammar follows the indicative conventions in the design
document's "Lexical and syntactic conventions" section and adopts
the new syntax that the acid-test sketches surfaced: `options` for
human choice gates, `runs` blocks on shell-actor states, `initial`
on artifact declarations, `source file` and `source path`
qualifiers, `uses profile` for profile composition, retry policy
on `error` and `timeout` outcomes, and the schema artifact
binding.

The reader should already be familiar with `orchestra-design.md`
and `orchestra-result-schemas.md`. This document does not
re-derive their conclusions; it codifies them.

## Goals

1. Specify the lexical rules for tokens, identifiers, literals,
   comments, and indentation.
2. Specify the grammar of a workflow file as a whole, from the
   `spec` line down to the contents of state bodies.
3. List the reserved words.
4. Specify the syntax of references (path expressions for
   artifact contents, state results, attempt counters, external
   inputs).
5. Specify how profile-registered backing-scoped keywords appear
   in the grammar without violating the closed-core rule.

## Non-goals

1. Defining new language features. This document codifies what
   the design document and result-schemas document already
   specified.
2. Specifying parser implementation. The grammar is given in EBNF
   for clarity; the choice of parser (hand-written recursive
   descent, ANTLR, tree-sitter) is a runner-spec concern.
3. Specifying error messages, recovery rules, or syntax
   highlighting affordances. Tooling concerns are out of scope.
4. Specifying the JSON Schema dialect for verdict and output
   schemas. Schemas are external files referenced by path; their
   content is governed by JSON Schema, not by this grammar.

## Notation

The grammar is given in EBNF with the following conventions:

- `'literal'`: a literal terminal token, matched verbatim.
- `Nonterminal`: a nonterminal, defined elsewhere in the grammar.
- `A | B`: alternation; either A or B.
- `A?`: optional; A appears zero or one times.
- `A*`: zero or more occurrences of A.
- `A+`: one or more occurrences of A.
- `(A B)`: grouping.
- `INDENT` / `DEDENT`: synthetic indentation tokens (see "Lexical
  rules" below).
- `NEWLINE`: end-of-line marker.

Lowercase token classes (`identifier`, `string`, `integer`, etc.)
are defined in "Lexical rules" below.

## Lexical rules

### Source encoding

Workflow files are UTF-8. The grammar treats the file as a
sequence of Unicode scalar values. Byte order marks at the start
of a file are accepted and discarded. No other byte-level handling
is part of the grammar.

### Whitespace and indentation

Indentation is significant. The lexer produces synthetic `INDENT`
and `DEDENT` tokens at points where the leading whitespace of a
line increases or decreases relative to a stack of indentation
levels. The rules:

1. The first non-blank, non-comment line of the file establishes
   the base indentation level (typically column 0). All
   subsequent indentation is measured against this baseline.
2. Indentation may be either spaces or tabs but must be consistent
   within a single file. Mixing the two is a load error.
3. A line whose leading whitespace is strictly greater than the
   current top of the indentation stack opens a new block: the
   lexer emits one `INDENT` token, pushes the new level onto the
   stack, and continues with the line's content.
4. A line whose leading whitespace is strictly less than the
   current top of the stack closes one or more blocks: the lexer
   pops levels off the stack until the top matches the line's
   leading whitespace and emits one `DEDENT` token per popped
   level. If no level on the stack matches the line's leading
   whitespace, the file is a load error.
5. A line whose leading whitespace equals the current top of the
   stack continues the current block.
6. End-of-file emits a `DEDENT` token for every level above the
   baseline.

Blank lines (containing only whitespace) and comment-only lines
do not affect the indentation stack.

Inside a multi-line string literal, indentation is preserved
verbatim and the indentation rules do not apply.

### Newlines

A logical newline is `\n`, `\r\n`, or `\r`, normalized to `\n` by
the lexer. The lexer emits a `NEWLINE` token at every logical
newline that is not inside a string literal or a continuation.

There are no line continuations in Orchestra. A statement that
needs to span multiple lines does so via grammar (e.g. `members`
list spilling across lines under indentation).

### Comments

A `#` character outside a string literal begins a comment that
extends to the end of the line. Comments are stripped by the
lexer and never appear in the token stream. There are no block
comments.

### Identifiers

```
identifier  ::=  letter (letter | digit | '_' | '-')*
letter      ::=  'A'..'Z' | 'a'..'z'
digit       ::=  '0'..'9'
```

Identifiers must begin with a letter. They may contain letters,
digits, underscores, and hyphens. They may not contain other
punctuation.

This rule applies to every user-defined name: workflow names,
state names, model names, role names, agent names, group names,
artifact names, profile names, schema artifact names, and the
names of options on a human choice gate.

The acid-test finding that ruled out `?` in identifiers (Test 1
A2) is reflected here: punctuation other than `_` and `-` is not
permitted.

### Reserved words

The following identifiers are reserved and may not be used as
user-defined names. They are listed grouped by where they appear
in the grammar.

Top-level keywords:

```
spec, workflow, model, role, agent, group, artifact, state,
prompt, profile, uses, max_total_steps, max_state_visits,
external_input, compression_model
```

Inside-state keywords:

```
actor, role, prompt, group, schema, reads, writes, options,
join, on, when, retry, max, then, timeout
```

(Note: `role` and `prompt` are reused as inside-state keywords;
they are unambiguous from context because they only appear after
`state`.)

Group declaration keywords:

```
kind, members
```

Artifact declaration keywords:

```
source, file, path, initial
```

Prompt source keywords:

```
file, template, with, from
```

Actor backing names (used as values, not as keywords; the parser
must treat these as ordinary identifiers in the binding position
and only the runner validates them against registered backings):

```
model, agent, shell, human, workflow
```

Outcome and status names (used as values):

```
complete, error, timeout, cancelled, pass, fail
```

Join policies:

```
all, any, quorum
```

Boolean literals:

```
true, false
```

Terminal targets:

```
done, stop
```

Other:

```
null
```

Profiles register additional reserved words via backing-scoped
keywords (see "Profile-registered keywords" below). Those words
are reserved only inside states whose actor backing the
registering profile covers; outside that scope they remain valid
as user-defined identifiers. Examples in v0: `mode`, `runs`,
`command`, `continue_on_fail`, `require_diff`, plus the value
identifiers `readwrite` and `readonly` that appear as operands of
`mode`.

### String literals

```
string         ::=  short_string | long_string
short_string   ::=  '"' short_string_char* '"'
short_string_char
               ::=  any char except '"', '\', or NEWLINE
                |   '\' escape_char
escape_char    ::=  '"' | '\' | 'n' | 'r' | 't' | '0'
long_string    ::=  '"""' long_string_char* '"""'
long_string_char
               ::=  any char except the closing '"""'
                |   '\' escape_char
```

Short strings are single-line and use `\"`, `\\`, `\n`, `\r`,
`\t`, `\0` as escapes. Long strings (triple-quoted) preserve
newlines and indentation verbatim and are intended for embedded
prompt text or shell command bodies that span multiple lines. No
interpolation is performed inside any string literal at the
grammar level.

### Numeric literals

```
integer  ::=  '-'? digit+
decimal  ::=  '-'? digit+ '.' digit+
```

Integers and decimals are written in standard decimal notation.
No hexadecimal, octal, binary, or exponential notation in v0.

### Duration literals

```
duration ::=  integer ('ms' | 's' | 'm' | 'h')
```

Duration literals appear in `timeout` declarations. The unit
suffix is required. Examples: `15m`, `30s`, `1h`, `500ms`.

### Boolean and null literals

```
boolean   ::=  'true' | 'false'
null_lit  ::=  'null'
```

### References (path expressions)

```
reference  ::=  identifier ('.' identifier)*
```

References resolve against the runtime context in ways pinned
down in "Reference resolution" below. The grammar accepts any
dotted identifier sequence; the runner validates it against
declared external inputs, artifact names, and the result
envelope.

## Top-level grammar

```
File
  ::=  SpecLine WorkflowDecl

SpecLine
  ::=  'spec' version NEWLINE

version
  ::=  integer ('.' integer)?
```

A workflow file consists of exactly two parts: a `spec` line and
a single `workflow` block. Every declaration the workflow uses
lives inside the workflow body. There is no separate outer
scope. Multi-workflow files and imports are reserved for v1; v0
parses one workflow per file.

The `spec` line is required and must be the first non-blank,
non-comment line of the file.

### Workflow declaration

```
WorkflowDecl
  ::=  'workflow' identifier NEWLINE INDENT WorkflowBody DEDENT

WorkflowBody
  ::=  (
         ProfileUseDecl
       | ExternalInputDecl
       | MaxStepsDecl
       | CompressionModelDecl
       | ModelDecl
       | RoleDecl
       | AgentDecl
       | GroupDecl
       | ArtifactDecl
       | StateDecl
       )+
```

Declarations inside the workflow body may appear in any order.
Validation rules in `orchestra-design.md` and the result-schemas
document govern ordering constraints (e.g. agents reference
models that must be declared somewhere; the validator builds a
dependency graph at load time, not at parse time).

### Profile use

```
ProfileUseDecl
  ::=  'uses' 'profile' identifier NEWLINE
```

A workflow may declare any number of `uses profile` lines.
Profiles are loaded in declaration order. Conflicting
registrations between profiles are load errors per validation
rule 12 of the design document.

Examples:

```
uses profile versioned-workspace
uses profile code
```

### External input

```
ExternalInputDecl
  ::=  'external_input' identifier type NEWLINE

type
  ::=  'text' | 'json' | 'integer' | 'decimal' | 'boolean'
   |   identifier        # a profile-registered artifact type
```

The `type` production names a primitive type or a
profile-registered artifact type. For v0 the recognized
primitive types are `text`, `json`, `integer`, `decimal`,
`boolean`. The versioned-workspace profile registers
`git-workspace` as a type usable in artifact declarations (and
external inputs of that type are accepted on profiles that admit
them).

`json` external inputs may carry a schema in v1; the
acid-test finding (Test 3 F21) flagged this as a known gap. v0
admits no schema qualifier on external inputs.

### Max steps

```
MaxStepsDecl
  ::=  ('max_total_steps' | 'max_state_visits') integer NEWLINE
```

Either keyword is accepted; they are synonyms. Validation rule
11 of the design document requires this declaration on every
workflow.

### Compression model

```
CompressionModelDecl
  ::=  'compression_model' identifier NEWLINE
```

Names a model identifier the runner uses for context-history
compression. Optional. When omitted, the runner's documented
default is used.

### Model declaration

```
ModelDecl
  ::=  'model' identifier NEWLINE
```

Models in workflow files are bare references to the model
registry. The registry maps short IDs to invocation commands and
provider configuration; that mapping lives outside the workflow
file (see the design document's "Model" section).

### Role declaration

```
RoleDecl
  ::=  'role' identifier NEWLINE INDENT RoleBody DEDENT

RoleBody
  ::=  PromptSourceDecl
```

A role declares its default prompt source. The prompt source
syntax is shared with state-level prompt overrides; see "Prompt
sources" below.

### Agent declaration

```
AgentDecl
  ::=  'agent' identifier NEWLINE INDENT AgentBody DEDENT

AgentBody
  ::=  AgentField+

AgentField
  ::=  'model' identifier NEWLINE
   |   'adapter' identifier NEWLINE
   |   'context_policy' identifier NEWLINE
```

An agent declares the model it wraps, its adapter, and a context
policy reference. The adapter and context policy values are
identifiers naming runner-registered choices; the validator
checks them against the runner's registry at load time.

### Group declaration

```
GroupDecl
  ::=  'group' identifier NEWLINE INDENT GroupBody DEDENT

GroupBody
  ::=  KindDecl MembersDecl

KindDecl
  ::=  'kind' ('roles' | 'agents') NEWLINE

MembersDecl
  ::=  'members' identifier (',' identifier)* NEWLINE
   |   'members' NEWLINE INDENT (identifier (',' identifier)* NEWLINE)+ DEDENT
```

Both `kind` and `members` are required. The single-line
`members` form is used for short member lists; the
multi-line form supports lists that exceed a comfortable line
length.

### Artifact declaration

```
ArtifactDecl
  ::=  'artifact' identifier type NEWLINE
       (INDENT ArtifactQualifier+ DEDENT)?

ArtifactQualifier
  ::=  'source' SourceQualifier NEWLINE
   |   'initial' Literal NEWLINE

SourceQualifier
  ::=  'file' string
   |   'path' identifier         # references an external_input of type text
   |   'path' string             # literal filesystem path
```

The `source` qualifier specifies that the artifact's content
comes from an external location. `source file <string>` reads the
file at the given path at load time. `source path <identifier>`
binds the artifact to a directory whose path is provided by the
named external input. `source path <string>` binds to a literal
filesystem path. The exact runtime semantics for each combination
are defined in the design document and the versioned-workspace
profile.

The `initial` qualifier specifies a starting value for the
artifact at workflow start (Test 2 F15). The literal must match
the artifact's declared type.

```
Literal
  ::=  string | integer | decimal | boolean | null_lit
   |   '[]' | '{}'
```

The `[]` and `{}` literals stand for empty list and empty object
respectively, useful for `initial` on `messages` and `json`
artifacts.

### Prompt sources

```
PromptSourceDecl
  ::=  'prompt' PromptSource NEWLINE

PromptSource
  ::=  'file' string
   |   'template' string ('with' identifier (',' identifier)*)?
   |   'from' reference
```

The three forms correspond to the three prompt sources in the
design document:

- `prompt file <path>`: a static file used verbatim as the prompt.
- `prompt template <path> with <var>, <var>...`: a template
  applied to the named values at invocation time. The variables
  must appear in the state's `reads` (or be external inputs) per
  validation rule 7.
- `prompt from <state>.<artifact-field>`: a reference to a prompt
  artifact produced by a prior state. The reference resolves
  against the result envelope of the named state.

### State declaration

```
StateDecl
  ::=  'state' identifier NEWLINE INDENT StateBody DEDENT

StateBody
  ::=  StateClause+

StateClause
  ::=  ActorClause
   |   RoleClause
   |   PromptClause
   |   GroupClause
   |   SchemaClause
   |   ReadsClause
   |   WritesClause
   |   OptionsClause
   |   JoinClause
   |   TimeoutClause
   |   TransitionClause
   |   BackingScopedClause
```

The clauses may appear in any order, but for readability the
indicative ordering used in the acid tests is: actor, role,
prompt, group, schema, backing-scoped clauses (mode, etc.),
reads, writes, options, join, timeout, transitions.

`mode` is intentionally not in the core clause list. It is
registered by the versioned-workspace profile as a backing-scoped
clause (see "Backing-scoped clauses" below). This matches the
design document's positioning of `mode` as a versioned-workspace
concern, not a universal state concept.

```
ActorClause
  ::=  'actor' ActorBacking NEWLINE

ActorBacking
  ::=  'model' identifier
   |   'agent' identifier
   |   'shell'
   |   'human'

RoleClause
  ::=  'role' identifier NEWLINE

PromptClause
  ::=  PromptSourceDecl

GroupClause
  ::=  'group' identifier NEWLINE

SchemaClause
  ::=  'schema' identifier NEWLINE

ReadsClause
  ::=  'reads' reference (',' reference)* NEWLINE

WritesClause
  ::=  'writes' identifier type NEWLINE

OptionsClause
  ::=  'options' identifier (',' identifier)* NEWLINE

JoinClause
  ::=  'join' JoinPolicy NEWLINE

JoinPolicy
  ::=  'all' | 'any' | ('quorum' integer)

TimeoutClause
  ::=  'timeout' duration NEWLINE
```

`WritesClause` may appear multiple times within a state body
(Test 1 A7: a state may write more than one artifact, including
a new version of an artifact written by an upstream state).

`ReadsClause` may also appear multiple times; the reads union
across all clauses is the state's effective `reads` set.

### Transitions

```
TransitionClause
  ::=  'on' identifier GuardClause? TransitionTarget NEWLINE
   |   'on' identifier RetryClause NEWLINE

GuardClause
  ::=  'when' GuardExpr

TransitionTarget
  ::=  '=>' (identifier | 'done' | 'stop')

RetryClause
  ::=  'retry' 'max' integer 'then' (identifier | 'done' | 'stop')
```

The `retry max N then <target>` form (Test 3 F19) is grammar
sugar for guarded retries. It is legal only on `error` and
`timeout` outcomes; the validator rejects its use on other
outcomes. Combining a guard with a retry clause is reserved for
v1; v0's grammar does not admit `on <outcome> when <guard> retry
max N then <target>`.

The guard expression grammar is intentionally minimal in v0:

```
GuardExpr
  ::=  GuardOr

GuardOr
  ::=  GuardAnd ('or' GuardAnd)*

GuardAnd
  ::=  GuardUnary ('and' GuardUnary)*

GuardUnary
  ::=  '!' GuardUnary
   |   GuardPrimary

GuardPrimary
  ::=  Comparison
   |   reference                    # truthy test on the reference
   |   '(' GuardExpr ')'

Comparison
  ::=  reference CmpOp Operand

CmpOp
  ::=  '<' | '<=' | '>' | '>=' | '==' | '!='

Operand
  ::=  Literal | reference
```

The grammar makes `and` bind tighter than `or` (the standard
precedence). In v0, mixing `and` and `or` in the same guard
without parentheses is a style-guide warning issued by the
validator: the precedence is well-defined, but readers
consistently mis-read the grouping under conventional precedence,
so the validator recommends parentheses whenever both operators
appear in a single guard. The warning is non-blocking.

The expression language is small on purpose. It covers the
acid-test cases (`attempts.continue-gate < 6`,
`task.needs_tests`, `attempts.fix-check < 5`) without expanding
into a full embedded expression language, which is a v0 non-goal
per the design document.

### Backing-scoped clauses

```
BackingScopedClause
  ::=  ModeClause
   |   RunsClause
   |   CommandClause
   |   ContinueOnFailClause
   |   RequireDiffClause
   |   <other clauses registered by profiles>

ModeClause
  ::=  'mode' identifier NEWLINE

RunsClause
  ::=  'runs' NEWLINE INDENT (string NEWLINE)+ DEDENT
   |   'runs' string (',' string)* NEWLINE

CommandClause
  ::=  'command' string NEWLINE

ContinueOnFailClause
  ::=  'continue_on_fail' boolean NEWLINE

RequireDiffClause
  ::=  'require_diff' boolean NEWLINE
```

Backing-scoped clauses are admitted by the grammar in any state
body, but the validator rejects them when the state's actor
backing or referenced artifacts do not match the registering
profile's scope. Specifically:

- `mode` is registered by the versioned-workspace profile and is
  legal only inside states that read or write a `git-workspace`
  artifact. The validator checks that the value (`readwrite` or
  `readonly`) is one of the modes the profile registered.
- `runs`, `command`, `continue_on_fail` are registered by the
  code profile and are legal only inside `actor shell` states.
- `require_diff` is registered by the code profile and is legal
  only inside states that write a `git-workspace` artifact under
  `mode readwrite`.

The grammar does not enforce these scoping rules; the validator
does, per validation rule 12 of the design document.

This is the "profile extension applies only inside the body of a
state whose actor backing the profile registers" rule from the
design document expressed in grammatical terms: the parser
admits any backing-scoped keyword anywhere a state clause is
allowed, and the validator rejects misuse at load time. This
keeps the parser context-free.

## Reference resolution

Every reference in the grammar (`reference` in the productions
above) is a dotted identifier sequence. Resolution is done at
load time for static references and at runtime for dynamic ones.
Every reference falls into one of the following categories,
disambiguated by the leading identifier.

### External input references

`task`, `topic`, `question`, `decision_id`, etc.

The leading identifier matches the name of an external input
declared at the workflow level. Subsequent identifiers index
into the input's structure (json fields, record components).

For `task.needs_tests`, the leading `task` matches an
`external_input task json` declaration; the field `needs_tests`
is resolved at runtime against the json value's keys.

### Artifact references

`draft`, `critique`, `verdict`, `chair-feedback`, etc.

The leading identifier matches the name of an artifact declared
at the workflow level. A bare artifact reference (no dotted
suffix) resolves to the latest version of that artifact, with
type-appropriate access (text content for `text`, parsed object
for `json`, message list for `messages`, etc.).

A dotted reference indexes into the artifact's structure where
the type permits: `verdict.feedback` reads the `feedback` field
of a json artifact named `verdict`. For artifacts whose type is
not structured (`text`, `git-workspace`), dotted access is a
load error.

### State result references

`<state>.outputs`, `<state>.<member>.<field>`,
`<state>.payload.<field>`, `<state>.attempt`, etc.

The leading identifier matches the name of a state declared in
the workflow. The reference resolves against the result envelope
of that state's most recent invocation, per the result-schemas
document.

`<state>.outputs` and `<state>.<member>.<field>` are the
multi-actor aggregate forms specified in the result-schemas
document's "Per-member references" section.

`<state>.payload.<field>` reads from the payload object;
`<state>.attempt`, `<state>.duration_ms`, `<state>.outcome` read
envelope-level fields. Both are admitted by the grammar; the
validator checks at load time that the field path exists on the
appropriate envelope or payload shape (per the result-schemas
document) for the state's actor backing.

State result references are the load-time entry point for
guards: a guard like `attempts.continue-gate < 6` resolves the
`attempts.continue-gate` reference against the runtime counter
table (see below), and a guard like
`synthesize.payload.verdict == "approve"` resolves against the
`synthesize` state's most recent envelope.

### Counter references

`attempts.<state>`, `retries.<state>`.

The leading identifier is `attempts` or `retries`. The dotted
suffix is a single state name. The reference resolves against
the runtime counter table per the result-schemas document's
"Counter semantics" section.

Counter references are valid only inside guard expressions on
transitions. Using them as artifact reads or template variables
is a load error.

### Special references

`done`, `stop`: terminal transition targets, not references.
They appear only on the right-hand side of `=>`.

`null`, `true`, `false`: literals, not references.

### Resolution order

When parsing a reference, the validator resolves the leading
identifier in this order:

1. Reserved (`attempts`, `retries`).
2. Declared state name in the workflow.
3. Declared artifact name in the workflow.
4. Declared external input name in the workflow.

A name that matches more than one of these categories is a load
error (an `external_input` and an `artifact` may not share a
name; same for state and artifact, and so on). The validator
enforces global name uniqueness across these declaration
categories.

## Profile-registered keywords

The grammar admits a fixed set of backing-scoped clauses
(`ModeClause`, `RunsClause`, `CommandClause`,
`ContinueOnFailClause`, `RequireDiffClause`) plus an open
extension point for future profiles. The mechanism is:

1. The parser admits any unknown clause-form
   `identifier (string | identifier | boolean | integer)?
   NEWLINE` inside a state body, treating it as a candidate
   backing-scoped clause.
2. At validation time, the validator looks up the leading
   identifier against the set of profile-registered
   backing-scoped keywords. If the identifier is registered for
   the state's actor backing or referenced artifact types, the
   clause is accepted and parsed according to the profile's
   specification. Otherwise it is a load error.

In v0, the backing-scoped clauses are registered as follows:

- `mode`: registered by the versioned-workspace profile, scoped
  to states that reference a `git-workspace` artifact.
- `runs`, `command`, `continue_on_fail`: registered by the code
  profile, scoped to `actor shell` states.
- `require_diff`: registered by the code profile, scoped to
  states that write a `git-workspace` artifact under
  `mode readwrite`.

Future profiles add to this list without grammar changes.

The parser is context-free with respect to backing-scoped
clauses: it admits the syntactic form, and the validator decides
whether the form is meaningful in the state's context. This
keeps the parser simple and lets profiles register clauses
without forking the grammar.

## Worked example

The following is a complete workflow file in the grammar
specified above, using Test 1's design loop as the source. It
is presented as a sanity check that the grammar admits the
acid-test sketches without modification.

```
spec 0.1

workflow design-loop

  external_input topic text

  max_total_steps 40

  model opus
  model gpt

  role designer
    prompt file prompts/designer.md

  role critic
    prompt file prompts/critic.md

  role reflector
    prompt file prompts/reflector.md

  agent claude-primary
    model opus
    adapter api_runner_managed
    context_policy default

  agent gpt-critic
    model gpt
    adapter api_runner_managed
    context_policy default

  artifact draft text
  artifact critique text
  artifact reflection text

  state draft
    actor agent claude-primary
    role designer
    prompt template prompts/designer-draft.md with topic
    reads topic
    writes draft text
    on complete => critique
    on error => stop
    on timeout => stop

  state critique
    actor agent gpt-critic
    role critic
    reads topic, draft
    writes critique text
    on complete => reflect
    on error => stop
    on timeout => stop

  state reflect
    actor agent claude-primary
    role reflector
    prompt template prompts/reflector.md with topic, draft, critique
    reads topic, draft, critique
    writes reflection text
    writes draft text
    on complete => continue-gate
    on error => stop
    on timeout => stop

  state continue-gate
    actor human
    prompt file prompts/continuation-question.md
    reads draft, critique, reflection
    options continue, accept, stop
    on continue when attempts.continue-gate < 6 => critique
    on continue => stop
    on accept => done
    on stop => stop
    on timeout => stop
    on cancelled => stop
```

The file parses against the grammar above with no extensions
needed. Test 2 (council) and Test 3 (mcloop) parse similarly,
with the addition of `uses profile` declarations and
backing-scoped clauses (`mode`, `runs`, `command`,
`continue_on_fail`, `require_diff`) inside the relevant states.

The agent name `claude-primary` is used here in place of the
acid-test's `claude-designer` because the same agent is invoked
as both designer and reflector in this workflow; a role-neutral
name reflects what the workflow actually does. This is a naming
preference, not a grammar requirement.

## Open questions

The following are deferred to runner spec or to a v1 grammar
revision.

1. **Triple-quoted strings in `runs` blocks.** A `runs` block
   containing long shell pipelines may benefit from triple-quoted
   strings rather than escaping inside short strings. The grammar
   admits `string` (which includes `long_string`); whether the
   ergonomics warrant standardizing on long strings inside `runs`
   is a documentation question, not a grammar one.

2. **`when` after `retry`.** A retry clause currently has no
   guard form; the syntax is `on error retry max 2 then abandon`,
   not `on error when <guard> retry max 2 then abandon`. If a
   future workflow needs to make retry conditional on a guard,
   the grammar will need to admit `GuardClause` before
   `RetryClause`. v0 does not require this.

3. **External input schemas.** The grammar admits
   `external_input task json` but no schema qualifier (Test 3
   F21). v1 may add `external_input task json schema <path>` or
   `external_input task record { ... }`. v0 leaves field
   references on json external inputs validated only at runtime.

4. **Multi-line guard expressions.** The grammar admits guard
   expressions that fit on one line. Long guards using `and` /
   `or` may benefit from line continuation; v0 does not provide
   one. Authors are expected to keep guards short enough to read.

5. **Reserved word evolution.** As profiles are added, the set
   of profile-registered keywords grows. The validator's
   registry handles this without grammar change, but workflow
   authors using identifiers that collide with future
   backing-scoped keywords will see their workflows break under
   profile additions. The workaround is to recommend that
   user-defined names not begin with `require_`, `continue_`,
   or other prefixes the profiles use as keyword roots. This is
   a style-guide concern, not a grammar one.

6. **Indentation semantics under tabs.** The grammar requires
   consistency within a file but does not specify whether tabs
   count as one column or eight. v0 leaves this to the lexer's
   implementation; the recommendation is to count each tab as
   one column and treat any tab/space mixture as a load error.

"""Parser for the slice-1 grammar subset.

Produces a ``Workflow`` (from ``orchestra.spine``) from a token stream.
The parser handles the subset of ``orchestra-grammar.md`` that the
``echo.orc`` fixture uses, plus enough of the rest to parse the three
acid-test sketches without further work in slice 2 (``uses profile``,
agent declarations, group declarations, schema clauses, backing-scoped
clauses).

Anything the parser does not yet recognize raises ``ParseError`` with
a clear message rather than silently dropping tokens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestra.errors import ParseError
from orchestra.loader.lexer import Lexer, Token
from orchestra.spine import (
    NO_INITIAL,
    ActorBinding,
    AgentDecl,
    AndExpr,
    ArtifactDecl,
    Comparison,
    ExternalInputDecl,
    GroupDecl,
    GuardExpr,
    Literal_,
    ModelDecl,
    NotExpr,
    OrExpr,
    PromptSource,
    Reference,
    RoleDecl,
    StateDecl,
    Transition,
    TruthyTest,
    Workflow,
    WriteDecl,
)


class Parser:
    def __init__(self, tokens: list[Token], source_path: Path) -> None:
        self._toks = tokens
        self._pos = 0
        self._source_path = source_path
        self._source_dir = str(source_path.parent.resolve())

    # ----- top level ---------------------------------------------

    def parse(self) -> Workflow:
        spec_version = self._parse_spec_line()
        wf = self._parse_workflow_block(spec_version)
        self._expect("EOF", consume=False)
        return wf

    def _parse_spec_line(self) -> str:
        self._expect_keyword("spec")
        major = self._expect("INT")
        version = major.value
        if self._peek().kind == "DOT":
            self._advance()
            minor = self._expect("INT")
            version = f"{major.value}.{minor.value}"
        self._expect("NEWLINE")
        return version

    def _parse_workflow_block(self, spec_version: str) -> Workflow:
        self._expect_keyword("workflow")
        name = self._expect("IDENT").value
        self._expect("NEWLINE")
        self._expect("INDENT")

        profiles: list[str] = []
        external_inputs: list[ExternalInputDecl] = []
        max_total_steps = 0
        compression_model: str | None = None
        models: list[ModelDecl] = []
        roles: list[RoleDecl] = []
        agents: list[AgentDecl] = []
        groups: list[GroupDecl] = []
        artifacts: list[ArtifactDecl] = []
        states: list[StateDecl] = []

        while self._peek().kind != "DEDENT" and self._peek().kind != "EOF":
            tok = self._peek()
            if tok.kind != "IDENT":
                raise ParseError(
                    f"unexpected token {tok.kind} {tok.value!r}",
                    line=tok.line,
                )
            kw = tok.value
            if kw == "uses":
                self._advance()
                self._expect_keyword("profile")
                pname = self._expect("IDENT").value
                self._expect("NEWLINE")
                profiles.append(pname)
            elif kw == "external_input":
                self._advance()
                ename = self._expect("IDENT").value
                etype = self._expect("IDENT").value
                self._expect("NEWLINE")
                external_inputs.append(ExternalInputDecl(name=ename, type=etype))
            elif kw == "max_total_steps" or kw == "max_state_visits":
                self._advance()
                value = int(self._expect("INT").value)
                self._expect("NEWLINE")
                if max_total_steps != 0:
                    raise ParseError(
                        "max_total_steps declared more than once",
                        line=tok.line,
                    )
                max_total_steps = value
            elif kw == "compression_model":
                self._advance()
                compression_model = self._expect("IDENT").value
                self._expect("NEWLINE")
            elif kw == "model":
                self._advance()
                mname = self._expect("IDENT").value
                self._expect("NEWLINE")
                models.append(ModelDecl(name=mname))
            elif kw == "role":
                roles.append(self._parse_role())
            elif kw == "agent":
                agents.append(self._parse_agent())
            elif kw == "group":
                groups.append(self._parse_group())
            elif kw == "artifact":
                artifacts.append(self._parse_artifact())
            elif kw == "state":
                states.append(self._parse_state())
            else:
                raise ParseError(
                    f"unexpected keyword {kw!r} at workflow scope",
                    line=tok.line,
                )

        self._expect("DEDENT")
        return Workflow(
            spec_version=spec_version,
            name=name,
            profiles=tuple(profiles),
            external_inputs=tuple(external_inputs),
            max_total_steps=max_total_steps,
            compression_model=compression_model,
            models=tuple(models),
            roles=tuple(roles),
            agents=tuple(agents),
            groups=tuple(groups),
            artifacts=tuple(artifacts),
            states=tuple(states),
            source_dir=self._source_dir,
        )

    # ----- declarations ------------------------------------------

    def _parse_role(self) -> RoleDecl:
        self._expect_keyword("role")
        name = self._expect("IDENT").value
        self._expect("NEWLINE")
        self._expect("INDENT")
        prompt = self._parse_prompt_clause()
        self._expect("DEDENT")
        return RoleDecl(name=name, default_prompt=prompt)

    def _parse_agent(self) -> AgentDecl:
        self._expect_keyword("agent")
        name = self._expect("IDENT").value
        self._expect("NEWLINE")
        self._expect("INDENT")
        model: str | None = None
        adapter: str | None = None
        context_policy: str | None = None
        while self._peek().kind != "DEDENT":
            tok = self._peek()
            if tok.kind != "IDENT":
                raise ParseError(
                    f"unexpected token in agent body: {tok.kind} {tok.value!r}",
                    line=tok.line,
                )
            kw = tok.value
            self._advance()
            if kw == "model":
                model = self._expect("IDENT").value
            elif kw == "adapter":
                adapter = self._expect("IDENT").value
            elif kw == "context_policy":
                context_policy = self._expect("IDENT").value
            else:
                raise ParseError(
                    f"unknown agent field: {kw!r}", line=tok.line
                )
            self._expect("NEWLINE")
        self._expect("DEDENT")
        if model is None:
            raise ParseError(f"agent {name!r}: missing 'model'", line=0)
        if adapter is None:
            raise ParseError(f"agent {name!r}: missing 'adapter'", line=0)
        if context_policy is None:
            raise ParseError(
                f"agent {name!r}: missing 'context_policy'", line=0
            )
        return AgentDecl(
            name=name,
            model=model,
            adapter=adapter,
            context_policy=context_policy,
        )

    def _parse_group(self) -> GroupDecl:
        self._expect_keyword("group")
        name = self._expect("IDENT").value
        self._expect("NEWLINE")
        self._expect("INDENT")
        kind: str | None = None
        members: list[str] = []
        while self._peek().kind != "DEDENT":
            tok = self._peek()
            if tok.kind != "IDENT":
                raise ParseError(
                    f"unexpected token in group body: {tok.kind} {tok.value!r}",
                    line=tok.line,
                )
            kw = tok.value
            self._advance()
            if kw == "kind":
                kind = self._expect("IDENT").value
                if kind not in ("roles", "agents"):
                    raise ParseError(
                        f"group kind must be 'roles' or 'agents', got {kind!r}",
                        line=tok.line,
                    )
                self._expect("NEWLINE")
            elif kw == "members":
                # Either inline (members a, b, c) or block.
                if self._peek().kind == "IDENT":
                    members.append(self._expect("IDENT").value)
                    while self._peek().kind == "COMMA":
                        self._advance()
                        members.append(self._expect("IDENT").value)
                    self._expect("NEWLINE")
                else:
                    self._expect("NEWLINE")
                    self._expect("INDENT")
                    while self._peek().kind == "IDENT":
                        members.append(self._expect("IDENT").value)
                        while self._peek().kind == "COMMA":
                            self._advance()
                            members.append(self._expect("IDENT").value)
                        self._expect("NEWLINE")
                    self._expect("DEDENT")
            else:
                raise ParseError(
                    f"unknown group field: {kw!r}", line=tok.line
                )
        self._expect("DEDENT")
        if kind is None:
            raise ParseError(f"group {name!r}: missing 'kind'", line=0)
        if not members:
            raise ParseError(f"group {name!r}: missing 'members'", line=0)
        return GroupDecl(name=name, kind=kind, members=tuple(members))  # type: ignore[arg-type]

    def _parse_artifact(self) -> ArtifactDecl:
        self._expect_keyword("artifact")
        name = self._expect("IDENT").value
        type = self._expect("IDENT").value
        self._expect("NEWLINE")
        initial: Any = NO_INITIAL
        source_kind: Any = None
        source_value: str | None = None
        if self._peek().kind == "INDENT":
            self._advance()
            while self._peek().kind != "DEDENT":
                kw = self._expect("IDENT").value
                if kw == "source":
                    sk = self._expect("IDENT").value
                    if sk == "file":
                        source_kind = "file"
                        source_value = self._expect("STRING").value
                    elif sk == "path":
                        source_kind = "path"
                        nxt = self._peek()
                        if nxt.kind == "STRING":
                            source_value = self._expect("STRING").value
                        else:
                            source_value = self._expect("IDENT").value
                    else:
                        raise ParseError(
                            f"unknown source qualifier: {sk!r}",
                            line=self._peek().line,
                        )
                    self._expect("NEWLINE")
                elif kw == "initial":
                    initial = self._parse_literal()
                    self._expect("NEWLINE")
                else:
                    raise ParseError(
                        f"unknown artifact qualifier: {kw!r}",
                        line=self._peek().line,
                    )
            self._expect("DEDENT")
        return ArtifactDecl(
            name=name,
            type=type,
            initial=initial,
            source_kind=source_kind,
            source_value=source_value,
        )

    def _parse_state(self) -> StateDecl:
        start_tok = self._peek()
        self._expect_keyword("state")
        name = self._expect("IDENT").value
        self._expect("NEWLINE")
        self._expect("INDENT")

        actor: ActorBinding | None = None
        role: str | None = None
        prompt: PromptSource | None = None
        reads: list[str] = []
        writes: list[WriteDecl] = []
        options: list[str] = []
        transitions: list[Transition] = []
        timeout_ms: int | None = None
        backing_options: dict[str, Any] = {}

        while self._peek().kind != "DEDENT":
            tok = self._peek()
            if tok.kind != "IDENT":
                raise ParseError(
                    f"unexpected token in state body: {tok.kind} {tok.value!r}",
                    line=tok.line,
                )
            kw = tok.value
            if kw == "actor":
                self._advance()
                kind = self._expect("IDENT").value
                ref: str | None = None
                if kind in ("model", "agent"):
                    ref = self._expect("IDENT").value
                elif kind in ("shell", "human"):
                    ref = None
                else:
                    raise ParseError(
                        f"unknown actor backing: {kind!r}", line=tok.line
                    )
                self._expect("NEWLINE")
                actor = ActorBinding(kind=kind, ref=ref)  # type: ignore[arg-type]
            elif kw == "role":
                self._advance()
                role = self._expect("IDENT").value
                self._expect("NEWLINE")
            elif kw == "prompt":
                prompt = self._parse_prompt_clause()
            elif kw == "group":
                self._advance()
                # Slice 1 doesn't exercise groups; consume and discard.
                self._expect("IDENT")
                self._expect("NEWLINE")
            elif kw == "schema":
                self._advance()
                self._expect("IDENT")
                self._expect("NEWLINE")
            elif kw == "reads":
                self._advance()
                reads.append(self._expect("IDENT").value)
                while self._peek().kind == "COMMA":
                    self._advance()
                    reads.append(self._expect("IDENT").value)
                self._expect("NEWLINE")
            elif kw == "writes":
                self._advance()
                wname = self._expect("IDENT").value
                wtype = self._expect("IDENT").value
                writes.append(WriteDecl(name=wname, type=wtype))
                self._expect("NEWLINE")
            elif kw == "options":
                self._advance()
                options.append(self._expect("IDENT").value)
                while self._peek().kind == "COMMA":
                    self._advance()
                    options.append(self._expect("IDENT").value)
                self._expect("NEWLINE")
            elif kw == "join":
                self._advance()
                self._expect("IDENT")
                if self._peek().kind == "INT":
                    self._advance()
                self._expect("NEWLINE")
            elif kw == "timeout":
                self._advance()
                value = int(self._expect("INT").value)
                unit = self._expect("IDENT").value
                self._expect("NEWLINE")
                timeout_ms = _duration_to_ms(value, unit, tok.line)
            elif kw == "on":
                transitions.append(self._parse_transition())
            elif kw == "mode":
                self._advance()
                backing_options["mode"] = self._expect("IDENT").value
                self._expect("NEWLINE")
            elif kw == "command":
                self._advance()
                backing_options["command"] = self._expect("STRING").value
                self._expect("NEWLINE")
            elif kw == "runs":
                self._advance()
                runs: list[str] = []
                if self._peek().kind == "STRING":
                    runs.append(self._expect("STRING").value)
                    while self._peek().kind == "COMMA":
                        self._advance()
                        runs.append(self._expect("STRING").value)
                    self._expect("NEWLINE")
                else:
                    self._expect("NEWLINE")
                    self._expect("INDENT")
                    while self._peek().kind == "STRING":
                        runs.append(self._expect("STRING").value)
                        self._expect("NEWLINE")
                    self._expect("DEDENT")
                backing_options["runs"] = runs
            elif kw == "continue_on_fail":
                self._advance()
                bv = self._expect("IDENT").value
                if bv not in ("true", "false"):
                    raise ParseError(
                        f"continue_on_fail expects true|false, got {bv!r}",
                        line=tok.line,
                    )
                backing_options["continue_on_fail"] = bv == "true"
                self._expect("NEWLINE")
            elif kw == "require_diff":
                self._advance()
                bv = self._expect("IDENT").value
                backing_options["require_diff"] = bv == "true"
                self._expect("NEWLINE")
            else:
                raise ParseError(
                    f"unknown state clause: {kw!r}", line=tok.line
                )

        self._expect("DEDENT")
        if actor is None:
            raise ParseError(
                f"state {name!r} has no 'actor' clause", line=start_tok.line
            )
        if actor.kind == "human" and options:
            backing_options["options"] = list(options)
        return StateDecl(
            name=name,
            actor=actor,
            role=role,
            prompt=prompt,
            reads=tuple(reads),
            writes=tuple(writes),
            options=tuple(options),
            transitions=tuple(transitions),
            timeout_ms=timeout_ms,
            backing_options=backing_options,
        )

    def _parse_prompt_clause(self) -> PromptSource:
        self._expect_keyword("prompt")
        kind = self._expect("IDENT").value
        if kind == "file":
            path = self._expect("STRING").value
            self._expect("NEWLINE")
            return PromptSource(kind="file", path=path)
        if kind == "template":
            path = self._expect("STRING").value
            template_vars: list[str] = []
            if self._peek().kind == "IDENT" and self._peek().value == "with":
                self._advance()
                template_vars.append(self._expect("IDENT").value)
                while self._peek().kind == "COMMA":
                    self._advance()
                    template_vars.append(self._expect("IDENT").value)
            self._expect("NEWLINE")
            return PromptSource(
                kind="template", path=path, template_vars=tuple(template_vars)
            )
        if kind == "from":
            head = self._expect("IDENT").value
            parts = [head]
            while self._peek().kind == "DOT":
                self._advance()
                parts.append(self._expect("IDENT").value)
            self._expect("NEWLINE")
            return PromptSource(kind="from", from_ref=".".join(parts))
        raise ParseError(f"unknown prompt source: {kind!r}", line=self._peek().line)

    def _parse_transition(self) -> Transition:
        on_tok = self._peek()
        self._expect_keyword("on")
        outcome = self._expect("IDENT").value
        guard: GuardExpr | None = None
        if self._peek().kind == "IDENT" and self._peek().value == "when":
            self._advance()
            guard = self._parse_guard_expr()
        if self._peek().kind == "ARROW":
            self._advance()
            target = self._expect("IDENT").value
            self._expect("NEWLINE")
            return Transition(outcome=outcome, target=target, guard=guard)
        if self._peek().kind == "IDENT" and self._peek().value == "retry":
            if guard is not None:
                raise ParseError(
                    "v0 grammar does not admit 'when <guard>' before 'retry'",
                    line=on_tok.line,
                )
            self._advance()
            self._expect_keyword("max")
            n_tok = self._expect("INT")
            n = int(n_tok.value)
            self._expect_keyword("then")
            target = self._expect("IDENT").value
            self._expect("NEWLINE")
            if outcome not in ("error", "timeout"):
                raise ParseError(
                    f"retry clause is legal only on 'error' or 'timeout', got {outcome!r}",
                    line=on_tok.line,
                )
            return Transition(
                outcome=outcome, target=target, guard=None, retry_max=n
            )
        raise ParseError(
            "expected '=> <target>' or 'retry max N then <target>'",
            line=self._peek().line,
        )

    # ----- guard expressions -------------------------------------

    def _parse_guard_expr(self) -> GuardExpr:
        return self._parse_or()

    def _parse_or(self) -> GuardExpr:
        first = self._parse_and()
        if not (
            self._peek().kind == "IDENT" and self._peek().value == "or"
        ):
            return first
        parts: list[GuardExpr] = [first]
        while self._peek().kind == "IDENT" and self._peek().value == "or":
            self._advance()
            parts.append(self._parse_and())
        return OrExpr(parts=tuple(parts))

    def _parse_and(self) -> GuardExpr:
        first = self._parse_unary()
        if not (
            self._peek().kind == "IDENT" and self._peek().value == "and"
        ):
            return first
        parts: list[GuardExpr] = [first]
        while self._peek().kind == "IDENT" and self._peek().value == "and":
            self._advance()
            parts.append(self._parse_unary())
        return AndExpr(parts=tuple(parts))

    def _parse_unary(self) -> GuardExpr:
        if self._peek().kind == "BANG":
            self._advance()
            return NotExpr(inner=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> GuardExpr:
        if self._peek().kind == "LPAREN":
            self._advance()
            inner = self._parse_or()
            self._expect("RPAREN")
            return inner
        ref = self._parse_reference()
        cmp_kinds = {"LT", "LE", "GT", "GE", "EQ", "NEQ"}
        if self._peek().kind in cmp_kinds:
            op_tok = self._advance()
            op = op_tok.value
            right = self._parse_operand()
            return Comparison(op=op, left=ref, right=right)
        return TruthyTest(ref=ref)

    def _parse_reference(self) -> Reference:
        head = self._expect("IDENT").value
        parts = [head]
        while self._peek().kind == "DOT":
            self._advance()
            parts.append(self._expect("IDENT").value)
        return Reference(parts=tuple(parts))

    def _parse_operand(self) -> Reference | Literal_:
        tok = self._peek()
        if tok.kind == "INT":
            self._advance()
            return Literal_(value=int(tok.value))
        if tok.kind == "STRING":
            self._advance()
            return Literal_(value=tok.value)
        if tok.kind == "IDENT":
            if tok.value in ("true", "false"):
                self._advance()
                return Literal_(value=tok.value == "true")
            if tok.value == "null":
                self._advance()
                return Literal_(value=None)
            return self._parse_reference()
        raise ParseError(
            f"expected operand, got {tok.kind}", line=tok.line
        )

    def _parse_literal(self) -> Any:
        tok = self._peek()
        if tok.kind == "INT":
            self._advance()
            return int(tok.value)
        if tok.kind == "STRING":
            self._advance()
            return tok.value
        if tok.kind == "IDENT":
            if tok.value in ("true", "false"):
                self._advance()
                return tok.value == "true"
            if tok.value == "null":
                self._advance()
                return None
        raise ParseError(
            f"expected literal, got {tok.kind} {tok.value!r}", line=tok.line
        )

    # ----- token helpers -----------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        idx = self._pos + offset
        if idx >= len(self._toks):
            return self._toks[-1]
        return self._toks[idx]

    def _advance(self) -> Token:
        tok = self._toks[self._pos]
        self._pos += 1
        return tok

    def _expect(self, kind: str, *, consume: bool = True) -> Token:
        tok = self._peek()
        if tok.kind != kind:
            raise ParseError(
                f"expected {kind}, got {tok.kind} {tok.value!r}",
                line=tok.line,
            )
        if consume:
            self._advance()
        return tok

    def _expect_keyword(self, kw: str) -> Token:
        tok = self._peek()
        if tok.kind != "IDENT" or tok.value != kw:
            raise ParseError(
                f"expected keyword {kw!r}, got {tok.kind} {tok.value!r}",
                line=tok.line,
            )
        return self._advance()


def _duration_to_ms(value: int, unit: str, line: int) -> int:
    table = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000}
    if unit not in table:
        raise ParseError(f"unknown duration unit: {unit!r}", line=line)
    return value * table[unit]


def parse_workflow(source: str, source_path: Path) -> Workflow:
    """Parse a workflow file's source text into a Workflow IR object."""
    tokens = Lexer(source).tokens()
    return Parser(tokens, source_path).parse()

"""Exception hierarchy for the runner.

Slice 1 aborts on errors with stack traces (per the implementation
plan's non-goal 7). The exception hierarchy exists so tests can assert
on specific failure modes and so a future diagnostic layer has a target.
"""

from __future__ import annotations


class OrchestraError(Exception):
    """Base for all runner errors."""


# --- Loader / validator ---------------------------------------------


class LoadError(OrchestraError):
    """A workflow could not be loaded.

    Covers parse errors, validation errors, and registry conflicts.
    The validation phase is recorded as ``phase`` for diagnostics.
    """

    def __init__(self, message: str, *, phase: str | None = None) -> None:
        super().__init__(message)
        self.phase = phase


class ParseError(LoadError):
    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message, phase="parse")
        self.line = line


class ValidationError(LoadError):
    pass


# --- Registry --------------------------------------------------------


class RegistryConflict(OrchestraError):
    pass


# --- Store -----------------------------------------------------------


class StoreError(OrchestraError):
    pass


# --- Adapter ---------------------------------------------------------


class AdapterError(OrchestraError):
    pass


# --- Executor --------------------------------------------------------


class ExecutorError(OrchestraError):
    pass


class StepBudgetExhausted(ExecutorError):
    pass


# --- Resume ----------------------------------------------------------


class ResumeError(OrchestraError):
    pass

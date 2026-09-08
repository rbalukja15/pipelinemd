"""Exceptions pipelinemd raises. The CLI maps each to a distinct exit code."""

from __future__ import annotations


class PipelinemdError(Exception):
    """Base class for every error this tool raises deliberately."""


class UsageError(PipelinemdError):
    """The invocation itself was wrong - bad URL, missing argument."""


class GitLabError(PipelinemdError):
    """The GitLab API refused, was unreachable, or answered unexpectedly."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(GitLabError):
    """Credentials are missing, wrong, or lack the needed scope."""


class NotFoundError(GitLabError):
    """The project, pipeline or job does not exist (or is not visible)."""


class DiagnosisError(PipelinemdError):
    """The LLM diagnosis layer could not produce a result."""

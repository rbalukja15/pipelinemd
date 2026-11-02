"""Exceptions pipelinemd raises. The CLI maps each to a distinct exit code."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Usage


class PipelinemdError(Exception):
    """Base class for every error this tool raises deliberately."""


class UsageError(PipelinemdError):
    """The invocation itself was wrong - bad URL, missing argument."""


class ApiError(PipelinemdError):
    """A CI provider's API refused, was unreachable, or answered unexpectedly."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitLabError(ApiError):
    """The GitLab API refused, was unreachable, or answered unexpectedly."""


class AuthError(GitLabError):
    """Credentials are missing, wrong, or lack the needed scope."""


class NotFoundError(GitLabError):
    """The project, pipeline or job does not exist (or is not visible)."""


class GitHubError(ApiError):
    """The GitHub API refused, was unreachable, or answered unexpectedly."""


class GitHubAuthError(GitHubError):
    """The token is missing, wrong, or lacks `actions: read`."""


class GitHubNotFoundError(GitHubError):
    """The repository, run or job does not exist (or the token cannot see it)."""


class RateLimitError(GitHubError):
    """GitHub's rate limit is spent until a reset time too far off to wait for."""


class DiagnosisError(PipelinemdError):
    """The LLM diagnosis layer could not produce a result.

    `usage` is set when the model answered and the answer was then refused -
    the call was billed even though no diagnosis came of it. It is None when
    no answer arrived at all.
    """

    def __init__(self, message: str, *, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage

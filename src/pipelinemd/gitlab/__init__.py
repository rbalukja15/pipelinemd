"""GitLab access: URL parsing, HTTP, and the REST calls we need."""

from .client import GitLabClient
from .http import HttpClient
from .url import Target, parse_target, rebase, target_from_parts

__all__ = [
    "GitLabClient",
    "HttpClient",
    "Target",
    "parse_target",
    "rebase",
    "target_from_parts",
]

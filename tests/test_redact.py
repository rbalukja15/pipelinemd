"""Credential masking. These run before anything leaves the process."""

from __future__ import annotations

import pytest

from pipelinemd.distill.redact import MASK, redact, redact_lines, redaction_patterns

from .fixtures.secrets import (
    AWS_KEY_ID,
    AWS_TEMP_KEY_ID,
    GITLAB_PAT_LONG,
    GITLAB_PAT_PLAIN,
    GITLAB_RUNNER_TOKEN,
    JWT,
    SLACK_TOKEN,
    URL_PASSWORD,
)


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (f"token={GITLAB_PAT_LONG}", GITLAB_PAT_LONG),
        (f"runner {GITLAB_RUNNER_TOKEN} registered", GITLAB_RUNNER_TOKEN),
        (f"AWS_ACCESS_KEY_ID={AWS_KEY_ID}", AWS_KEY_ID),
        (f"temp creds {AWS_TEMP_KEY_ID} here", AWS_TEMP_KEY_ID),
        (f"slack {SLACK_TOKEN}", SLACK_TOKEN),
        (f"Authorization: Bearer {JWT}", JWT),
        (f"https://user:{URL_PASSWORD}@gitlab.com/x.git", URL_PASSWORD),
        ("--password hunter2000 --user bob", "hunter2000"),
        ("--token abc123def456 deploy", "abc123def456"),
        ("DB_PASSWORD=correcthorse psql", "correcthorse"),
        ('api_key: "sk-abcdef123456"', "sk-abcdef123456"),
        ("CLIENT_SECRET=zzz9999yyy", "zzz9999yyy"),
    ],
)
def test_secret_shapes_are_masked(raw: str, secret: str) -> None:
    result = redact(raw)
    assert secret not in result
    assert MASK in result


@pytest.mark.parametrize(
    "raw",
    [
        "DEBUG=true",
        "SECRET=false",
        "password: ***",
        "token=[MASKED]",
        "installing 42 packages",
        "https://gitlab.com/acme/web.git",
        "error: connection to server at db.internal failed",
    ],
)
def test_benign_lines_are_left_alone(raw: str) -> None:
    assert redact(raw) == raw


def test_url_credentials_keep_the_surrounding_url() -> None:
    result = redact(f"git clone https://oauth2:{GITLAB_PAT_PLAIN}@gitlab.com/x/y.git")
    assert result == f"git clone https://oauth2:{MASK}@gitlab.com/x/y.git"


def test_redact_lines_preserves_line_count() -> None:
    lines = [
        "before",
        "-----BEGIN RSA PRIVATE KEY-----",
        "MIIEowIBAAKCAQEA",
        "3Tz2mr7SZiAMfQyu",
        "-----END RSA PRIVATE KEY-----",
        "after",
    ]
    result = redact_lines(lines)
    assert len(result) == len(lines), "evidence refers to lines by number"
    assert result[0] == "before"
    assert result[1] == "-----BEGIN RSA PRIVATE KEY-----"
    assert result[2] == MASK
    assert result[3] == MASK
    assert result[4] == "-----END RSA PRIVATE KEY-----"
    assert result[5] == "after"


def test_unterminated_key_block_masks_to_the_end() -> None:
    result = redact_lines(["-----BEGIN PRIVATE KEY-----", "aaaa", "bbbb"])
    assert result[1:] == [MASK, MASK]


def test_redaction_patterns_are_named() -> None:
    names = redaction_patterns()
    assert "private-key-block" in names
    assert len(names) == len(set(names))

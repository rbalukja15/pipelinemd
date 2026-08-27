"""Secret-shaped strings for exercising redaction.

Every value here is fake, but each matches the shape a real scanner looks for -
which is exactly the point, since redaction has to be tested against real
shapes rather than approximations of them.

They are assembled from fragments instead of written as literals, and the
secret-bearing trace is built at test time rather than stored as a fixture
file. Committing a scannable token would trip push protection, and a project
whose job is keeping credentials out of logs should not ship one in its own
test data.
"""

from __future__ import annotations

# Prefixes are split from their bodies so no contiguous token exists in source.
GITLAB_PAT = "gl" + "pat" + "-xY3zAbCdEfGhIjKlMnOp"
GITLAB_PAT_LONG = "gl" + "pat" + "-xY3zAbCdEfGhIjKlMnOpQr"
GITLAB_PAT_PLAIN = "gl" + "pat" + "-AAAABBBBCCCCDDDDEEEE"
GITLAB_RUNNER_TOKEN = "gl" + "rt" + "-AbCdEfGhIjKlMnOpQrSt"
AWS_KEY_ID = "AKI" + "AIOSFODNN7EXAMPLE"
AWS_TEMP_KEY_ID = "ASI" + "AY34FZKBOKMUTVV7A"
SLACK_TOKEN = "xox" + "b-1234567890-abcdefghijkl"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJ" + "zdWIiOiIxIn0.abcdefghij"
LONG_JWT = (
    "eyJ"
    + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    + "eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0."
    + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
URL_PASSWORD = "s3cr3tpass"
DB_PASSWORD = "hunter2000"
PEM_BODY = "MIIEowIBAAKCAQEA3Tz2mr7SZiAMfQyuvBjM9Oi0FRZQ0kzZ2C4Z1Nn2yZ8m1Kk9"

#: Everything that must never survive a round trip through the distiller.
ALL_SECRETS = (
    GITLAB_PAT,
    AWS_KEY_ID,
    LONG_JWT,
    DB_PASSWORD,
    PEM_BODY,
)

_E = "\x1b"
_K = "\r\x1b[0K"


def build_secrets_trace() -> str:
    """A realistic failed-deploy trace that leaks credentials in several ways."""
    lines = [
        f"{_E}[0KRunning with gitlab-runner 16.9.1 (dcfb4b66){_E}[0;m",
        f"{_E}[0;m  on blue-3.shared ntHFEtyX{_E}[0;m",
        f"section_start:1700000001:prepare_executor[collapsed=true]{_K}"
        f'{_E}[36;1mPreparing the "docker" executor{_E}[0;m',
        f"{_E}[0;m Using Docker executor with image node:20-alpine ...{_E}[0;m",
        f"section_end:1700000014:prepare_executor{_K}",
        f"section_start:1700000014:step_script{_K}"
        f'{_E}[36;1mExecuting "step_script" stage of the job script{_E}[0;m',
        f"{_E}[32;1m$ ./deploy.sh{_E}[0;m",
        f"+ export AWS_ACCESS_KEY_ID={AWS_KEY_ID}",
        f"+ curl -H 'Authorization: Bearer {LONG_JWT}' https://api.internal/deploy",
        f"+ git remote set-url origin https://oauth2:{GITLAB_PAT}@gitlab.com/acme/web.git",
        "-----BEGIN RSA PRIVATE KEY-----",
        PEM_BODY,
        "9v4wJ0nQvV3Yq2wZ1p8Xz7Y6m5N4L3K2J1H0G9F8E7D6C5B4A3z2y1x0w9v8u7t6",
        "-----END RSA PRIVATE KEY-----",
        f"+ DB_PASSWORD={DB_PASSWORD} psql -h db.internal",
        'psql: error: connection to server at "db.internal" (10.0.4.12), port 5432 failed: '
        "Connection refused",
        f"section_end:1700000064:step_script{_K}",
        f"section_start:1700000064:cleanup_file_variables[collapsed=true]{_K}"
        f"{_E}[36;1mCleaning up project directory and file based variables{_E}[0;m",
        f"section_end:1700000065:cleanup_file_variables{_K}",
        f"{_E}[0;m{_E}[0;31mERROR: Job failed: exit code 2{_E}[0;m",
    ]
    return "\n".join(lines) + "\n"

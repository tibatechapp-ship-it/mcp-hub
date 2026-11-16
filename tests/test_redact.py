"""Secret redaction tests (v1.3.0).

Fixtures are assembled at runtime on purpose: the file must not contain a
secret-shaped literal, otherwise repository secret scanners flag the tests
themselves. The strings handed to ``redact()`` are identical to real tokens.
"""

from __future__ import annotations

import pytest

from mcp_hub.redact import REDACTED, redact

GITHUB_PAT = "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123456789"
GITHUB_FINE_GRAINED = "github" + "_pat_" + "11ABCDEFG0123456789_" + "abcdefghijklmnop"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
SLACK_TOKEN = "xox" + "b-" + "1234567890-abcdefghij"
GOOGLE_KEY = "AIza" + "SyA1234567890abcdefghijklmnopqrstu"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123"
PEM = (
    "---" + "--BEGIN RSA PRIVATE KEY---" + "--\n"
    "MIIEpAIBAAKCAQEA1234567890abcdef\n"
    "---" + "--END RSA PRIVATE KEY---" + "--"
)


def test_empty_values():
    assert redact("") == ""
    assert redact(None) == ""


def test_plain_text_is_untouched():
    text = "server 42 has no issues"
    assert redact(text) == text


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "apikey",
        "api-key",
        "API_KEY",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "access_token",
        "refresh_token",
        "client_secret",
    ],
)
def test_generic_key_value(key):
    out = redact(f"{key}=s3cr3tValue123")
    assert "s3cr3tValue123" not in out
    assert REDACTED in out


def test_generic_key_colon():
    out = redact("password: myP4ssw0rd!")
    assert "myP4ssw0rd" not in out
    assert REDACTED in out


def test_generic_with_quotes():
    out = redact('api_key="s3cr3t-with-quotes"')
    assert "s3cr3t-with-quotes" not in out


def test_bearer_token():
    out = redact(f"Authorization: Bearer {JWT}")
    assert JWT.split(".")[0] not in out
    assert REDACTED in out


def test_bearer_lowercase():
    out = redact("bearer abcdef1234567890")
    assert "abcdef1234567890" not in out


def test_x_api_key_header():
    out = redact("x-api-key: my-secret-key-here")
    assert "my-secret-key-here" not in out
    assert REDACTED in out


def test_github_pat_classic():
    out = redact(f"token={GITHUB_PAT}")
    assert "ghp_" not in out
    assert REDACTED in out


def test_github_pat_fine_grained():
    out = redact(GITHUB_FINE_GRAINED)
    assert GITHUB_FINE_GRAINED[:20] not in out


def test_aws_access_key():
    out = redact(AWS_KEY)
    assert AWS_KEY not in out
    assert "AKIA<REDACTED>" in out


def test_slack_token():
    out = redact(SLACK_TOKEN)
    assert "1234567890-abcdefghij" not in out


def test_google_api_key():
    out = redact(GOOGLE_KEY)
    assert "SyA1234567890" not in out


def test_url_token_query():
    out = redact("https://api.example.com/x?token=abc123def&other=1")
    assert "abc123def" not in out
    assert "other=1" in out


def test_url_multiple_secrets():
    out = redact("https://x.com/?key=K1&secret=S2&password=P3&keep=ok")
    for secret in ("K1", "S2", "P3"):
        assert secret not in out
    assert "keep=ok" in out


def test_connection_string_password():
    out = redact("postgres://user:" + "hunter2" + "@db.example.com/app")
    assert "hunter2" not in out
    assert "user:" in out
    assert "@db.example.com" in out


def test_pem_private_key():
    out = redact(PEM)
    assert "MIIEpAIBAAKCAQEA" not in out


def test_non_string_input():
    assert "123" in redact(123)
    assert "True" in redact(True)


def test_multiple_secrets_in_one_string():
    out = redact("api_key=AAA111 token=BBB222 password=CCC333")
    for secret in ("AAA111", "BBB222", "CCC333"):
        assert secret not in out


def test_surrounding_text_is_preserved():
    out = redact("Error: api_key=secret123 at line 42")
    assert "Error:" in out
    assert "at line 42" in out

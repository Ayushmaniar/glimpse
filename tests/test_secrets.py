"""Key masking in result snippets and .env discovery (no model needed; all keys below are fake).

  .venv\\Scripts\\python.exe -m tests.test_secrets
"""
from __future__ import annotations

import sys

from glimpse import secretmask as sm
from glimpse.sources.walk import ext_of

FAKE_GOOGLE = "AIza" + "SyD3x9Kq7Lm2Np4Rt6Vw8Yz0Ab1Cd3Ef5Gh"

HIDE = [
    f"GEMINI_API_KEY={FAKE_GOOGLE}",
    f"gemini key: {FAKE_GOOGLE}",
    'OPENAI_API_KEY="sk-' + 'proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z"',
    "ANTHROPIC_API_KEY=sk-" + "ant-api03-Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2",
    '"password": "hunter2Pass!"',
    "db_password = S3cretP4ss",
    "Authorization: Bearer abcDEF123ghiJKL456mnoPQR789",
    "postgres://admin:pa55word@db.local:5432/app",
    "token=ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "HF_TOKEN=hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAx\nabc\n-----END RSA PRIVATE KEY-----",
    "aws_access_key_id = AKIA" + "IOSFODNN7EXAMPL3",
    "my weather api: 9f8B2kQ7xL3mZ5vR1tY6wN4pC0hJ8sD2gF7a",  # bare random token, no known prefix
]
KEEP = [
    'api_key = os.environ["GEMINI_API_KEY"]',
    "GEMINI_API_KEY=your-api-key-here",
    "token = get_token(user)",
    "password: str",
    "commit 3f9a1c2b7d4e5f60718293a4b5c6d7e8f9a0b1c2 fixed the bug",
    "id 123e4567-e89b-12d3-a456-426614174000",
    "The quick brown fox jumps over the lazy dog many times today.",
    "def compute_embedding_for_the_given_document_text(x):",
    "export GEMINI_API_KEY=$GEMINI_API_KEY",
    "see https://example.com/path/to/SomeLongDocumentationPage2024Edition",
    "class AbstractSingletonProxyFactoryBean2Builder:",
    "C:/Users/me/Projects/MyVeryLongProjectNameForTesting2024Q3/src",
]
ENV = {".env": ".env", ".env.local": ".env", ".ENV.production": ".env", "prod.env": ".env",
       "a.py": ".py", "env.txt": ".txt", ".envrc": ""}


def main() -> None:
    fails = 0
    for t in HIDE:
        m = sm.mask(t)
        ok = m != t and not any(v in m for v in sm.values(t) if len(v) > 6)
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} hidden: {t[:48]!r}")
    for t in KEEP:
        ok = sm.mask(t) == t
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} left alone: {t[:48]!r}" + ("" if ok else f" -> {sm.mask(t)!r}"))
    # "Copy key" copies exactly the value that was masked
    vals = sm.values(f"GEMINI_API_KEY={FAKE_GOOGLE}\nOTHER=x")
    ok = vals == [FAKE_GOOGLE]
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} copy value is the full key")
    for name, want in ENV.items():
        ok = ext_of(name) == want
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} ext_of({name!r}) = {ext_of(name)!r}")
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

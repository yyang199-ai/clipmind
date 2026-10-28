"""Extractor tests — the instant-intelligence layer."""

from clipmind.extractor import (
    KIND_COLOR,
    KIND_COMMAND,
    KIND_EMAIL,
    KIND_JSON,
    KIND_OTP,
    KIND_PHONE,
    KIND_TABLE,
    KIND_URL,
    extract,
    looks_like_secret,
)


def test_otp_from_sms():
    ex = extract("【星河银行】您的验证码是 483920，10分钟内有效。")
    assert ex.kind == KIND_OTP
    assert ex.meta["otp"] == "483920"


def test_otp_standalone():
    ex = extract("483920")
    assert ex.kind == KIND_OTP
    assert ex.meta["otp"] == "483920"


def test_english_verification_code():
    ex = extract("Your verification code is 8842. Do not share it.")
    assert ex.kind == KIND_OTP
    assert ex.meta["otp"] == "8842"


def test_url_standalone():
    ex = extract("https://github.com/user/repo")
    assert ex.kind == KIND_URL


def test_url_in_text():
    ex = extract("check this out https://example.com/a?b=1 thanks")
    assert ex.kind != KIND_URL  # it is a sentence, not a bare link
    assert "https://example.com/a?b=1" in ex.meta["urls"]


def test_email():
    ex = extract("sales@example.com")
    assert ex.kind == KIND_EMAIL


def test_phone():
    ex = extract("13800138000")
    assert ex.kind == KIND_PHONE


def test_phone_with_country_code():
    ex = extract("+86 138 0013 8000")
    assert ex.kind == KIND_PHONE


def test_hex_color():
    ex = extract("#3B82F6")
    assert ex.kind == KIND_COLOR
    assert ex.meta["color"] == "#3B82F6"


def test_rgb_color():
    ex = extract("rgb(59, 130, 246)")
    assert ex.kind == KIND_COLOR


def test_json():
    ex = extract('{"model": "gpt-4o", "temperature": 0.7}')
    assert ex.kind == KIND_JSON
    assert "model" in ex.meta.get("json_keys", [])


def test_json_array():
    ex = extract("[1, 2, 3, 42]")
    assert ex.kind == KIND_JSON


def test_command():
    assert extract("git status").kind == KIND_COMMAND
    assert extract("docker run -it ubuntu bash").kind == KIND_COMMAND
    assert extract("$ pip install fastembed").kind == KIND_COMMAND


def test_plain_text_not_command():
    assert extract("how to install fastembed please").kind != KIND_COMMAND


def test_code():
    ex = extract("def hello():\n    print('hi')\n    return 0\n")
    assert ex.kind in ("code", "markdown")


def test_table():
    ex = extract("| name | age |\n|------|-----|\n| tom  | 12  |")
    assert ex.kind == KIND_TABLE


def test_plain_text():
    ex = extract("just a normal sentence about lunch plans")
    assert ex.kind == "text"


def test_entities_collected():
    ex = extract("email me at bob@corp.io or visit https://corp.io")
    assert "bob@corp.io" in ex.meta["emails"]
    assert "https://corp.io" in ex.meta["urls"]


# ---------------------------------------------------------------- secrets

def test_secret_totp_uri():
    assert looks_like_secret("otpauth://totp/Example:alice?secret=JBSWY3DP")


def test_secret_github_pat():
    assert looks_like_secret("ghp_" + "x" * 36)


def test_secret_jwt():
    assert looks_like_secret("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123DEF_")


def test_secret_high_entropy_password():
    assert looks_like_secret("Xk9#mP2$vLq7Zw")


def test_not_secret_sentences():
    assert not looks_like_secret("hello world")
    assert not looks_like_secret("The quick brown fox jumps over the lazy dog")
    assert not looks_like_secret("https://github.com/some/repo")
    assert not looks_like_secret("13800138000")
    assert not looks_like_secret("random")

"""The Razorpay client and settings, without a database."""

import pytest
from pydantic import ValidationError

from gharbasket.modules.billing.razorpay import valid_signature

from ..conftest import DEPLOYED_SECRETS, make_settings

# Razorpay's own example (razorpay-node wiki): this body and secret give this signature.
BODY = b'{"a":1,"b":2,"c":{"d":3}}'
SIGNATURE = "2fe04e22977002e6c7cb553adab8b460cb9e2a4970d5953cb27a8472752e3bbc"


def test_the_webhook_signature_matches_razorpays_published_example() -> None:
    assert valid_signature(BODY, SIGNATURE, "123456")
    assert not valid_signature(BODY, SIGNATURE, "654321")
    assert not valid_signature(BODY + b" ", SIGNATURE, "123456")
    assert not valid_signature(BODY, "", "123456")


LIVE_KEYS = {
    "razorpay_key_id": "rzp_live_Abc123",
    "razorpay_key_secret": "fake-7c1d",
    "razorpay_webhook_secret": "fake-9e2a",
}


def refused(**settings: object) -> str:
    """The start-up error's text, after checking that it never shows a key or a secret."""
    with pytest.raises(ValidationError) as error:
        make_settings(**settings)
    message = str(error.value)
    for value in ("Abc123", "7c1d", "9e2a", "Zz9"):
        assert value not in message
    return message


def test_test_mode_is_the_default_and_takes_only_test_keys() -> None:
    assert make_settings().razorpay_mode == "test"
    assert make_settings(razorpay_key_id="rzp_test_Abc123").razorpay_key_id == "rzp_test_Abc123"
    assert not make_settings(razorpay_key_id="rzp_test_Abc123").razorpay_enabled
    for env in ("dev", "test", "staging"):
        deployed = DEPLOYED_SECRETS if env == "staging" else {}
        for key in ("rzp_live_Abc123", "rzp_test_", "rzp_test_Abc 123", "key_Zz9"):
            message = refused(app_env=env, **deployed, razorpay_key_id=key)
            assert "RAZORPAY_KEY_ID must be a test key (rzp_test_…) when RAZORPAY_MODE=test" in (
                message
            )


def test_production_is_live_only_and_starts_without_keys_with_payments_off() -> None:
    """ADR-0039: production never takes test keys; before its live keys exist it runs without."""
    off = make_settings(app_env="prod", **DEPLOYED_SECRETS)
    assert (off.razorpay_mode, off.razorpay_enabled) == ("test", False)
    for key in ("rzp_test_Abc123", "rzp_live_Abc123"):
        for extra in ({}, {"razorpay_key_secret": "fake-7c1d"}):
            message = refused(app_env="prod", **DEPLOYED_SECRETS, razorpay_key_id=key, **extra)
            assert "APP_ENV=prod takes only live Razorpay keys" in message
    for name, value in (
        ("razorpay_key_secret", "fake-7c1d"),
        ("razorpay_webhook_secret", "fake-9e2a"),
    ):
        message = refused(app_env="prod", **DEPLOYED_SECRETS, **{name: value})
        assert "APP_ENV=prod takes only live Razorpay keys" in message
        assert name.upper() in message


@pytest.mark.parametrize("env", ["prod"])
def test_live_mode_needs_production_live_keys_and_both_secrets(env: str) -> None:
    live = make_settings(app_env=env, **DEPLOYED_SECRETS, razorpay_mode="live", **LIVE_KEYS)
    assert (live.razorpay_mode, live.razorpay_enabled) == ("live", True)
    for key in ("rzp_test_Abc123", "rzp_live_", "key_Zz9"):
        message = refused(
            app_env=env,
            **DEPLOYED_SECRETS,
            razorpay_mode="live",
            **{**LIVE_KEYS, "razorpay_key_id": key},
        )
        assert "RAZORPAY_KEY_ID must be a live key (rzp_live_…) when RAZORPAY_MODE=live" in message
    for name in LIVE_KEYS:
        without = {k: v for k, v in LIVE_KEYS.items() if k != name}
        message = refused(app_env=env, **DEPLOYED_SECRETS, razorpay_mode="live", **without)
        assert f"{name.upper()} required when RAZORPAY_MODE=live" in message
    for name in ("razorpay_key_secret", "razorpay_webhook_secret"):
        message = refused(
            app_env=env, **DEPLOYED_SECRETS, razorpay_mode="live", **{**LIVE_KEYS, name: ""}
        )
        assert f"{name.upper()} required when RAZORPAY_MODE=live" in message
    message = refused(app_env=env, **DEPLOYED_SECRETS, razorpay_mode="live")
    assert (
        "RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET required when "
        "RAZORPAY_MODE=live"
    ) in message


@pytest.mark.parametrize("env", ["dev", "test", "staging"])
def test_live_mode_is_refused_outside_production(env: str) -> None:
    """Staging keeps test money for good (ADR-0039), so the team can test payments any time."""
    deployed = DEPLOYED_SECRETS if env == "staging" else {}
    message = refused(app_env=env, **deployed, razorpay_mode="live", **LIVE_KEYS)
    assert "RAZORPAY_MODE=live needs APP_ENV=prod" in message


def test_an_unknown_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAZORPAY_MODE", "real")
    assert "razorpay_mode" in refused()
    monkeypatch.setenv("RAZORPAY_MODE", "live")
    assert "RAZORPAY_MODE=live needs APP_ENV=prod" in refused()

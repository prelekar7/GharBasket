"""The Razorpay client and settings, without a database."""

import pytest
from pydantic import ValidationError

from gharbasket.modules.billing.razorpay import valid_signature

from ..conftest import make_settings

# Razorpay's own example (razorpay-node wiki): this body and secret give this signature.
BODY = b'{"a":1,"b":2,"c":{"d":3}}'
SIGNATURE = "2fe04e22977002e6c7cb553adab8b460cb9e2a4970d5953cb27a8472752e3bbc"


def test_the_webhook_signature_matches_razorpays_published_example() -> None:
    assert valid_signature(BODY, SIGNATURE, "123456")
    assert not valid_signature(BODY, SIGNATURE, "654321")
    assert not valid_signature(BODY + b" ", SIGNATURE, "123456")
    assert not valid_signature(BODY, "", "123456")


def test_only_test_mode_keys_are_accepted() -> None:
    assert make_settings(razorpay_key_id="rzp_test_Abc123").razorpay_key_id == "rzp_test_Abc123"
    assert not make_settings(razorpay_key_id="rzp_test_Abc123").razorpay_enabled
    with pytest.raises(ValidationError):
        make_settings(razorpay_key_id="rzp_live_Abc123")

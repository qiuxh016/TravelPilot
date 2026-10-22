from decimal import Decimal

from travelpilot_api.advisor.costs import estimate_cost_microusd
from travelpilot_api.settings import Settings


def test_estimates_cost_from_explicit_operator_rates():
    assert estimate_cost_microusd(
        input_tokens=1_000_000,
        output_tokens=500_000,
        input_rate_per_million=Decimal("0.25"),
        output_rate_per_million=Decimal("1.00"),
    ) == 750_000


def test_does_not_guess_cost_when_any_rate_is_missing():
    assert estimate_cost_microusd(
        input_tokens=100,
        output_tokens=50,
        input_rate_per_million=Decimal("0.25"),
        output_rate_per_million=None,
    ) is None


def test_rejects_invalid_usage_instead_of_reporting_zero_cost():
    assert estimate_cost_microusd(
        input_tokens=True,
        output_tokens=50,
        input_rate_per_million=Decimal("0.25"),
        output_rate_per_million=Decimal(1),
    ) is None


def test_rounds_to_nearest_micro_usd():
    assert estimate_cost_microusd(
        input_tokens=1,
        output_tokens=0,
        input_rate_per_million=Decimal("0.49"),
        output_rate_per_million=Decimal(0),
    ) == 0
    assert estimate_cost_microusd(
        input_tokens=1,
        output_tokens=0,
        input_rate_per_million=Decimal("0.50"),
        output_rate_per_million=Decimal(0),
    ) == 1


def test_blank_optional_environment_rates_are_treated_as_unconfigured():
    settings = Settings(
        _env_file=None,
        llm_input_cost_per_million_usd="",
        llm_output_cost_per_million_usd="",
    )
    assert settings.llm_input_cost_per_million_usd is None
    assert settings.llm_output_cost_per_million_usd is None

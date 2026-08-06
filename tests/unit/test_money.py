import pytest

from starkbank_trial.domain.errors import InvalidAmountError
from starkbank_trial.domain.money import calculate_net_amount
from starkbank_trial.domain.types import Cents


def test_calculate_net_amount_returns_received_amount_less_fee() -> None:
    # Given
    amount = Cents(10_000)
    fee = Cents(50)

    # When
    result = calculate_net_amount(amount, fee)

    # Then
    assert result == Cents(9_950)


@pytest.mark.parametrize(("amount", "fee"), [(100, 100), (100, 101), (100, -1), (0, 0)])
def test_calculate_net_amount_rejects_non_positive_or_malformed_values(
    amount: int,
    fee: int,
) -> None:
    # Given
    received_amount = Cents(amount)
    charged_fee = Cents(fee)

    # When / Then
    with pytest.raises(InvalidAmountError):
        calculate_net_amount(received_amount, charged_fee)

from starkbank_trial.domain.errors import InvalidAmountError
from starkbank_trial.domain.types import Cents


def calculate_net_amount(amount: Cents, fee: Cents) -> Cents:
    if amount <= 0 or fee < 0 or fee >= amount:
        raise InvalidAmountError(amount=amount, fee=fee)
    return Cents(amount - fee)

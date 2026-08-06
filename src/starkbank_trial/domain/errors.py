from dataclasses import dataclass

from starkbank_trial.domain.types import Cents


class TrialError(Exception):
    pass


@dataclass(slots=True)
class InvalidAmountError(TrialError):
    amount: Cents
    fee: Cents

    def __str__(self) -> str:
        return f"invalid financial amounts: amount={self.amount}, fee={self.fee}"


@dataclass(slots=True)
class InvalidBatchCountError(TrialError):
    counts: tuple[int, ...]

    def __str__(self) -> str:
        return "a trial requires eight batch counts between 8 and 12"


@dataclass(slots=True)
class InvalidStartTimeError(TrialError):
    def __str__(self) -> str:
        return "the trial start time must include a timezone"


@dataclass(slots=True)
class MissingProviderConfigurationError(TrialError):
    missing_fields: tuple[str, ...]

    def __str__(self) -> str:
        return f"missing provider configuration: {', '.join(self.missing_fields)}"

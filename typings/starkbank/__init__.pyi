from collections.abc import Iterator

class Project:
    def __init__(self, id: str, environment: str, private_key: str) -> None: ...

class Invoice:
    amount: int
    tax_id: str
    name: str
    id: str | None
    tags: list[str] | None
    def __init__(
        self,
        amount: int,
        tax_id: str,
        name: str,
        tags: list[str] | None = ...,
    ) -> None: ...

class Transfer:
    amount: int
    name: str
    tax_id: str
    bank_code: str
    branch_code: str
    account_number: str
    account_type: str
    id: str | None
    external_id: str | None
    status: str | None
    tags: list[str] | None
    def __init__(
        self,
        amount: int,
        name: str,
        tax_id: str,
        bank_code: str,
        branch_code: str,
        account_number: str,
        account_type: str,
        external_id: str | None = ...,
        tags: list[str] | None = ...,
        description: str | None = ...,
    ) -> None: ...

class Webhook:
    id: str | None
    url: str
    subscriptions: list[str]
    def __init__(
        self,
        url: str,
        subscriptions: list[str],
        id: str | None = ...,
    ) -> None: ...

class _InvoiceApi:
    def create(self, invoices: list[Invoice], user: Project | None = ...) -> list[Invoice]: ...
    def query(
        self,
        limit: int | None = ...,
        tags: list[str] | None = ...,
        user: Project | None = ...,
    ) -> Iterator[Invoice]: ...

class _TransferApi:
    def create(self, transfers: list[Transfer], user: Project | None = ...) -> list[Transfer]: ...
    def query(
        self,
        limit: int | None = ...,
        tags: list[str] | None = ...,
        user: Project | None = ...,
    ) -> Iterator[Transfer]: ...

class _EventApi:
    def parse(self, content: str, signature: str, user: Project | None = ...) -> object: ...

class _WebhookApi:
    def create(
        self,
        url: str,
        subscriptions: list[str],
        user: Project | None = ...,
    ) -> Webhook: ...
    def query(
        self,
        limit: int | None = ...,
        user: Project | None = ...,
    ) -> Iterator[Webhook]: ...

class _KeyApi:
    def create(self) -> tuple[str, str]: ...

invoice: _InvoiceApi
transfer: _TransferApi
event: _EventApi
webhook: _WebhookApi
key: _KeyApi

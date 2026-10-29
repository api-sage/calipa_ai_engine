"""Typed client for the FCY Payment Processor API (read endpoints only).

Response models reject unknown fields: if the bank changes the API, runs fail loudly
with SchemaDrift instead of silently auditing data they don't understand. The raw
body is always already stored by the time parsing happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from calipa.gateway.http import ApiResponse, GatewayError, ReadOnlyClient
from calipa.money import Currency, MoneyError, canonical_decimal, loads_decimal, to_decimal

Amount = Annotated[Decimal, BeforeValidator(to_decimal)]
AccountNumber = Annotated[str, Field(pattern=r"^[0-9]{10}$")]

MAX_PAGE_SIZE = 100


class UpstreamError(GatewayError):
    def __init__(self, message: str, response: ApiResponse) -> None:
        super().__init__(f"{message} (api call {response.call_id})")
        self.response = response


class AuthError(UpstreamError):
    pass


class RequestRejected(UpstreamError):
    """HTTP 400: the API rejected our parameters."""

    def __init__(self, errors: list[str], response: ApiResponse) -> None:
        super().__init__(f"request rejected: {errors}", response)
        self.errors = errors


class SchemaDrift(UpstreamError):
    """The response no longer matches the shape this client was written against."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class GLEntry(_Strict):
    id: UUID
    transfer_id: UUID = Field(alias="transferId")
    external_reference: str = Field(alias="externalReference", max_length=64)
    debited_account: AccountNumber = Field(alias="debitedAccount")
    credited_account: AccountNumber = Field(alias="creditedAccount")
    entry_type: Literal["DEBIT", "CREDIT"] = Field(alias="entryType")
    currency: Currency
    amount: Amount
    created_at: AwareDatetime = Field(alias="createdAt")


class GLPage(_Strict):
    items: list[GLEntry]
    page: int
    page_size: int = Field(alias="pageSize")
    total: int = Field(ge=0)
    total_pages: int = Field(alias="totalPages", ge=0)


class TransactionLookup(_Strict):
    transaction_reference: str = Field(alias="transactionReference")
    items: list[GLEntry]
    total: int = Field(ge=0)


class Rate(_Strict):
    id: int
    from_currency: Currency = Field(alias="fromCurrency")
    to_currency: Currency = Field(alias="toCurrency")
    rate: Amount
    rate_date: date = Field(alias="rateDate")
    created_at: AwareDatetime = Field(alias="createdAt")


class Charges(_Strict):
    amount: Amount
    currency: Currency
    charge: Amount
    vat: Amount
    sum_total: Amount = Field(alias="sumTotal")


class ParticipantBank(_Strict):
    bank_name: str = Field(alias="bankName")
    bank_code: str = Field(alias="bankCode", pattern=r"^[0-9]{6}$")


@dataclass(frozen=True, slots=True)
class Fetched[T]:
    data: T
    response: ApiResponse


class FcyClient:
    def __init__(self, http: ReadOnlyClient) -> None:
        self._http = http

    def gl_page(
        self,
        *,
        start: date,
        end: date,
        page: int,
        page_size: int = MAX_PAGE_SIZE,
        gl_account: str | None = None,
        run_id: UUID | None = None,
    ) -> Fetched[GLPage]:
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be 1..{MAX_PAGE_SIZE}")
        params = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "page": str(page),
            "pageSize": str(page_size),
        }
        if gl_account is not None:
            params["glAccountNumber"] = gl_account
        response = self._http.get("/get-gl-transactions", params, run_id=run_id)
        return Fetched(_parse(GLPage, _data(response), response), response)

    def transaction(
        self, reference: str, *, run_id: UUID | None = None
    ) -> Fetched[TransactionLookup]:
        if not 1 <= len(reference) <= 64:
            raise ValueError("transaction reference must be 1..64 characters")
        response = self._http.get(
            "/get-transaction", {"transactionReference": reference}, run_id=run_id
        )
        return Fetched(_parse(TransactionLookup, _data(response), response), response)

    def rates(self, *, run_id: UUID | None = None) -> Fetched[list[Rate]]:
        response = self._http.get("/get-rates", run_id=run_id)
        data = _data(response)
        if not isinstance(data, list):
            raise SchemaDrift("rates: expected a list", response)
        return Fetched([_parse(Rate, item, response) for item in data], response)

    def charges(
        self, amount: Decimal, currency: Currency, *, run_id: UUID | None = None
    ) -> Fetched[Charges]:
        response = self._http.get(
            "/get-charges",
            {"amount": canonical_decimal(amount), "fromCurrency": currency.value},
            run_id=run_id,
        )
        return Fetched(_parse(Charges, _data(response), response), response)

    def participant_banks(self, *, run_id: UUID | None = None) -> Fetched[list[ParticipantBank]]:
        response = self._http.get("/get-participant-banks", run_id=run_id)
        data = _data(response)
        if not isinstance(data, list):
            raise SchemaDrift("participant banks: expected a list", response)
        return Fetched([_parse(ParticipantBank, item, response) for item in data], response)


def _data(response: ApiResponse) -> Any:
    """Unwrap the API's {success, message, data} envelope, mapping its error shapes."""
    if response.status_code == 401:
        raise AuthError("credentials rejected", response)
    try:
        body = loads_decimal(response.body)
    except (ValueError, MoneyError) as exc:
        raise UpstreamError(
            f"HTTP {response.status_code}, body is not valid JSON: {exc}", response
        ) from exc
    if response.status_code == 400:
        errors = body.get("errors") if isinstance(body, dict) else None
        raise RequestRejected([str(e) for e in errors or [body]], response)
    if response.status_code != 200:
        raise UpstreamError(f"unexpected HTTP {response.status_code}", response)
    if not isinstance(body, dict) or set(body) - {"success", "message", "data"}:
        raise SchemaDrift("unexpected envelope", response)
    if body.get("success") is not True:
        raise UpstreamError(f"API reported failure: {body.get('message')!r}", response)
    if "data" not in body:
        raise SchemaDrift("envelope has no data", response)
    return body["data"]


def _parse[M: BaseModel](model: type[M], data: Any, response: ApiResponse) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SchemaDrift(f"{model.__name__}: {exc}", response) from exc

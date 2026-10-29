"""Versioned reference data: account roles, the fee schedule, the control catalog and
entitlements.

The API has no chart of accounts and only exposes the current tariff, so Calipa keeps
its own versioned copies. Each file's SHA-256 is recorded with any run that uses it.
The v1 files are inferred or drafted and still need confirming by their owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from importlib import resources
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from calipa.canonical import sha256_hex
from calipa.engine.contract import ExceptionType
from calipa.money import Currency, Money, to_decimal


class Objective(StrEnum):
    TRANSIENT_ACCOUNT_RECONCILIATION = "transient_account_reconciliation"
    FEE_VAT_LEG_COMPLETENESS = "fee_vat_leg_completeness"
    FX_CONVERSION_CHECK = "fx_conversion_check"
    DUPLICATE_DETECTION = "duplicate_detection"
    TRANSACTION_TRACE = "transaction_trace"


class AccountRole(StrEnum):
    TRANSIENT = "TRANSIENT"
    FEE_INCOME = "FEE_INCOME"
    VAT = "VAT"


@dataclass(frozen=True, slots=True)
class AccountCatalog:
    version: str
    sha256: str
    roles: dict[str, AccountRole]  # account number -> role
    descriptions: dict[AccountRole, str]  # what each role is, without account numbers

    def role(self, account_number: str) -> AccountRole | None:
        return self.roles.get(account_number)

    def account_for(self, role: AccountRole) -> str:
        matches = [number for number, r in self.roles.items() if r is role]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one {role} account, found {len(matches)}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class Tariff:
    version: str
    sha256: str
    flat_charge: Money
    vat_rate: Decimal
    rounding: str


@dataclass(frozen=True, slots=True)
class Control:
    control_id: str
    name: str
    exception_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ControlCatalog:
    version: str
    sha256: str
    controls: dict[str, Control]
    objectives: dict[Objective, tuple[str, ...]]  # objective -> control ids


@dataclass(frozen=True, slots=True)
class Grant:
    """What one person may do: the union of their roles."""

    can_initiate: bool
    can_review: bool
    account_roles: frozenset[AccountRole]
    objectives: frozenset[Objective]
    max_period_days: int


NO_GRANT = Grant(False, False, frozenset(), frozenset(), 0)


@dataclass(frozen=True, slots=True)
class Entitlements:
    version: str
    sha256: str
    roles: dict[str, Grant]
    users: dict[str, tuple[str, ...]]

    def grant_for(self, user: str) -> Grant:
        """Unknown users get nothing."""
        grants = [self.roles[r] for r in self.users.get(user, ())]
        if not grants:
            return NO_GRANT
        return Grant(
            can_initiate=any(g.can_initiate for g in grants),
            can_review=any(g.can_review for g in grants),
            account_roles=frozenset().union(*(g.account_roles for g in grants)),
            objectives=frozenset().union(*(g.objectives for g in grants)),
            max_period_days=max(g.max_period_days for g in grants),
        )


_DecimalText = Annotated[Decimal, BeforeValidator(to_decimal)]


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Account(_File):
    account_number: str = Field(pattern=r"^[0-9]{10}$")
    role: AccountRole
    description: str


class _AccountsFile(_File):
    version: str
    status: str
    source: str
    accounts: list[_Account]


class _Amount(_File):
    amount: _DecimalText
    currency: Currency


class _TariffFile(_File):
    version: str
    status: str
    source: str
    valid_from: date
    flat_charge: _Amount
    vat_rate: _DecimalText
    vat_base: Literal["principal"]
    rounding: Literal["ROUND_HALF_UP", "ROUND_HALF_EVEN"]


class _Control(_File):
    control_id: str = Field(pattern=r"^C-[A-Z-]+$")
    name: str
    exception_types: list[str] = Field(min_length=1)


class _ControlsFile(_File):
    version: str
    status: str
    source: str
    controls: list[_Control]
    objectives: dict[Objective, list[str]]


class _Role(_File):
    can_initiate: bool
    can_review: bool
    account_roles: list[AccountRole]
    objectives: list[Objective]
    max_period_days: int = Field(ge=0)


class _EntitlementsFile(_File):
    version: str
    status: str
    roles: dict[str, _Role]
    users: dict[str, list[str]]


_ROUNDING = {"ROUND_HALF_UP": ROUND_HALF_UP, "ROUND_HALF_EVEN": ROUND_HALF_EVEN}


def _read(name: str) -> bytes:
    return resources.files(__name__).joinpath(name).read_bytes()


def load_account_catalog(version: int = 1) -> AccountCatalog:
    raw = _read(f"accounts.v{version}.json")
    parsed = _AccountsFile.model_validate_json(raw)
    roles: dict[str, AccountRole] = {}
    descriptions: dict[AccountRole, str] = {}
    for account in parsed.accounts:
        if account.account_number in roles:
            raise ValueError(f"account {account.account_number} listed twice")
        roles[account.account_number] = account.role
        descriptions.setdefault(account.role, account.description)
    return AccountCatalog(
        version=parsed.version, sha256=sha256_hex(raw), roles=roles, descriptions=descriptions
    )


def load_tariff(version: int = 1) -> Tariff:
    raw = _read(f"tariff.v{version}.json")
    parsed = _TariffFile.model_validate_json(raw)
    return Tariff(
        version=parsed.version,
        sha256=sha256_hex(raw),
        flat_charge=Money(parsed.flat_charge.amount, parsed.flat_charge.currency),
        vat_rate=parsed.vat_rate,
        rounding=_ROUNDING[parsed.rounding],
    )


def load_control_catalog(version: int = 1) -> ControlCatalog:
    raw = _read(f"controls.v{version}.json")
    parsed = _ControlsFile.model_validate_json(raw)
    controls: dict[str, Control] = {}
    for c in parsed.controls:
        if c.control_id in controls:
            raise ValueError(f"control {c.control_id} listed twice")
        for t in c.exception_types:
            ExceptionType(t)  # raises on a type the engine does not know
        controls[c.control_id] = Control(c.control_id, c.name, tuple(c.exception_types))
    if set(parsed.objectives) != set(Objective):
        raise ValueError("every objective needs a control list")
    for ids in parsed.objectives.values():
        unknown = set(ids) - set(controls)
        if unknown:
            raise ValueError(f"unknown controls {sorted(unknown)}")
    return ControlCatalog(
        version=parsed.version,
        sha256=sha256_hex(raw),
        controls=controls,
        objectives={o: tuple(ids) for o, ids in parsed.objectives.items()},
    )


def load_entitlements(version: int = 1) -> Entitlements:
    raw = _read(f"entitlements.v{version}.json")
    parsed = _EntitlementsFile.model_validate_json(raw)
    for user, roles in parsed.users.items():
        unknown = set(roles) - set(parsed.roles)
        if unknown:
            raise ValueError(f"user {user} has unknown roles {sorted(unknown)}")
    return Entitlements(
        version=parsed.version,
        sha256=sha256_hex(raw),
        roles={
            name: Grant(
                can_initiate=r.can_initiate,
                can_review=r.can_review,
                account_roles=frozenset(r.account_roles),
                objectives=frozenset(r.objectives),
                max_period_days=r.max_period_days,
            )
            for name, r in parsed.roles.items()
        },
        users={user: tuple(roles) for user, roles in parsed.users.items()},
    )

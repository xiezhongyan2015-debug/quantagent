"""Integer stock-distribution rights on the frozen raw ledger, no tax inference.

This is a physical-rights adapter, not a research execution backend. Acquisition
dates, corporate-credit EOD netting, fractional allocations and final taxes are
unresolved. Generated events remain simulated; issuer dates are not account
receipts. Net performance admission is explicitly unavailable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, time
from decimal import Decimal, localcontext
from pathlib import Path
import hashlib
import re

from .kernel import module

_CA = module("corporate_action_adapter")
_RL = module("raw_share_ledger")
AdapterError = _CA.AdapterError
UnsupportedCorporateAction = _CA.UnsupportedCorporateAction
RawShareLedger = _RL.RawShareLedger
LedgerError = _RL.LedgerError
VERSION = "integer-stock-distribution-rights-v1"
TAX_GAPS = (
    "new-share tax acquisition date and holding-period lineage",
    "corporate credits versus trading EOD netting and prelisting disposal",
    "historically applicable fractional-share allocation and tax cent rounding",
)


@dataclass(frozen=True)
class StockDistributionAnnouncement:
    action_id: str
    symbol: str
    announcement_date: str
    record_date: str
    ex_date: str
    cash_payment_date: str
    stock_listing_date: str
    gross_cash_per_share: str
    bonus_per_share: str
    capitalization_per_share: str
    bonus_par_value_cny: str
    capitalization_source: str
    source_url: str
    source_sha256: str
    source_fetched_at: str
    facts_verified: bool
    account_type: str = _CA.ACCOUNT_TYPE
    implementation_status: str = "implementation_notice"
    rights_issue: bool = False


class StockDistributionRightsAdapter:
    """Stateless, exact-integer physical events; never manufacture a tax lot."""

    def __init__(self, announcement: StockDistributionAnnouncement, trading_days: list[str], *,
                 source_document: str | Path, price_basis: str = "raw"):
        if type(announcement) is not StockDistributionAnnouncement:
            raise AdapterError("StockDistributionAnnouncement required")
        self._spec = announcement
        if price_basis != "raw":
            raise UnsupportedCorporateAction("stock rights require raw prices")
        if (announcement.facts_verified is not True or announcement.rights_issue is not False
                or announcement.account_type != _CA.ACCOUNT_TYPE
                or announcement.implementation_status != "implementation_notice"):
            raise UnsupportedCorporateAction("verified implemented ordinary unrestricted individual account scope required")
        if type(announcement.symbol) is not str or not re.fullmatch(r"sh\d{6}", announcement.symbol):
            raise UnsupportedCorporateAction("this physical-rights policy is restricted to the reviewed Shanghai market")
        for value, limit in ((announcement.action_id, 120), (announcement.source_url, 2000)):
            if type(value) is not str or not value.strip() or len(value) > limit:
                raise AdapterError("invalid action identity/source URL")
        if type(announcement.source_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", announcement.source_sha256):
            raise AdapterError("document SHA256 required")
        _CA._time(announcement.source_fetched_at)
        path = Path(source_document)
        if not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
            raise AdapterError("bounded local source document required")
        payload = path.read_bytes()
        if not payload.startswith(b"%PDF") or hashlib.sha256(payload).hexdigest() != announcement.source_sha256:
            raise AdapterError("source PDF identity mismatch")
        if type(trading_days) is not list or not trading_days:
            raise AdapterError("explicit ordered calendar required")
        sessions = [_CA._day(day) for day in trading_days]
        if sessions != sorted(set(sessions)):
            raise AdapterError("calendar must be unique and increasing")
        self._days = list(trading_days)
        announced, record, ex, paid, listed = [_CA._day(getattr(announcement, field)) for field in
            ("announcement_date", "record_date", "ex_date", "cash_payment_date", "stock_listing_date")]
        if not date(2015, 9, 8) <= record or not announced < record < ex <= min(paid, listed):
            raise AdapterError("inconsistent or unsupported corporate dates")
        if any(day not in sessions for day in (record, ex, paid, listed)):
            raise AdapterError("all event dates require calendar coverage")
        def next_day(day):
            found = next((value for value in sessions if value > day), None)
            if found is None:
                raise AdapterError("calendar lacks required next session")
            return found
        self._times = {
            "announcement_available_at": _CA._at(next_day(announced), time.min),
            "record_at": _CA._at(record, time(15)),
            "activate_at": _CA._at(ex, time(8)),
            "cash_available_at": _CA._at(next_day(paid), time(8)),
            "stock_listed_at": _CA._at(listed, time(9, 30)),
        }
        if self._times["announcement_available_at"] > self._times["record_at"]:
            raise AdapterError("simulated announcement availability after registration")
        self._cash = _CA._decimal(announcement.gross_cash_per_share, positive=True)
        self._bonus = _CA._decimal(announcement.bonus_per_share)
        self._capital = _CA._decimal(announcement.capitalization_per_share)
        self._par = _CA._decimal(announcement.bonus_par_value_cny, positive=True)
        if not 0 < self._bonus + self._capital <= 100:
            raise UnsupportedCorporateAction("bounded nonzero stock distribution required")
        expected_source = "share_premium_verified" if self._capital else "none"
        if announcement.capitalization_source != expected_source:
            raise UnsupportedCorporateAction("capitalization requires separate verified share-premium origin")
        self._share_kind = "mixed" if self._bonus and self._capital else "bonus" if self._bonus else "capitalization"
        self._policy = {
            "version": VERSION, "announcement": asdict(announcement), "calendar": self._days,
            "price_basis": "raw", "times": self._times,
            "cash_availability": "simulated next supplied session after issuer payment date at08:00",
            "listing_sellability": "simulated issuer listing date at09:30; not tax acquisition",
            "rounding": "exact per-component integer shares and exact account cents only",
            "taxable_income": "cash plus bonus shares at source-reviewed par; share-premium capitalization excluded",
            "tax_assessment": "unimplemented; no zero-tax substitute, no certified net NAV",
            "source_boundary": "controller-attested facts and local PDF identity; not authentication or historical arrival",
            "tax_gaps": list(TAX_GAPS),
        }
        self._policy_hash = _CA._hash(self._policy)

    @property
    def spec(self):
        return self._spec

    def manifest(self):
        # Avoid allowing a caller to mutate the policy behind its event hashes.
        import copy
        return {**copy.deepcopy(self._policy), "assumption_sha256": self._policy_hash,
                "tax_lineage_implemented": False, "research_execution_ready": False,
                "execution_valid": False}

    def entitlements(self, registered_quantity: int):
        _CA._quantity(registered_quantity)
        with localcontext() as context:
            context.prec = 60
            bonus = Decimal(registered_quantity) * self._bonus
            capital = Decimal(registered_quantity) * self._capital
            if bonus % 1 or capital % 1:
                raise UnsupportedCorporateAction("fractional bonus/capitalization allocation is unverified; no floor, ceiling or cross-component netting")
            total = int(bonus + capital)
            _CA._quantity(total)
            gross = _CA._cents(Decimal(registered_quantity) * self._cash)
            try:
                _RL._money(gross, positive=True)
            except LedgerError as exc:
                raise AdapterError("cash entitlement exceeds raw ledger bounds") from exc
            taxable = Decimal(gross) + bonus * self._par
            return {"registered_shares": registered_quantity, "cash_gross_due": gross,
                    "bonus_shares": int(bonus), "capitalization_shares": int(capital),
                    "new_shares": total, "share_kind": self._share_kind,
                    "bonus_taxable_income_cny_exact": str(bonus * self._par),
                    "capitalization_taxable_income_cny_exact": "0",
                    "total_taxable_income_cny_exact": str(taxable),
                    "maximum_tax_exposure_cny_exact": str(taxable * Decimal("0.20")),
                    "maximum_is_exposure_only_not_assessed_or_paid_tax": True}

    def _source(self):
        return {"ref": self.spec.source_url, "sha256": self.spec.source_sha256, "verified": True,
                "evidence_type": "simulated", "assumption_ref": "frozen-policy://" + VERSION,
                "assumption_sha256": self._policy_hash}

    def _event(self, stage, kind, when, data):
        return {"event_id": self.spec.action_id + ":" + stage, "kind": kind,
                "effective_at": when, "available_at": max(when, self._times["announcement_available_at"]),
                "source": self._source(), "data": {"action_id": self.spec.action_id, **data}}

    def _ledger(self, ledger, at):
        if not isinstance(ledger, RawShareLedger) or ledger.genesis["trading_days"] != self._days:
            raise AdapterError("identical frozen raw ledger calendar required")
        state = ledger.snapshot()
        if state["last_applied_at"] is not None and _CA._time(state["last_applied_at"]) > _CA._time(at):
            raise AdapterError("cannot generate earlier stage from later account")
        return state

    def _expected_events(self, quantity):
        e = self.entitlements(quantity)
        return {
            "record": self._event("record", "record_entitlement", self._times["record_at"],
                {"symbol": self.spec.symbol, "entitled_shares": quantity}),
            "activate": self._event("activate", "activate_entitlement", self._times["activate_at"],
                {"cash_gross_due": e["cash_gross_due"], "tax_due": None, "tax_final": False,
                 "bonus_shares": e["new_shares"], "share_kind": e["share_kind"]}),
            "cash": self._event("cash", "cash_dividend_paid", self._times["cash_available_at"],
                {"gross_amount": e["cash_gross_due"], "net_cash_credit": e["cash_gross_due"], "tax_withheld": "0.00"}),
            "listed": self._event("listed", "bonus_shares_listed", self._times["stock_listed_at"],
                {"quantity": e["new_shares"]}),
        }

    def _action(self, ledger, at, *, active):
        state = self._ledger(ledger, at)
        action = state["actions"].get(self.spec.action_id)
        if action is None or action["active"] is not active:
            raise AdapterError("registration/activation stage mismatch")
        if (action["symbol"] != self.spec.symbol
                or _CA._time(action["registered_at"]) != _CA._time(self._times["record_at"])):
            raise AdapterError("registered action disagrees with source dates/identity")
        expected = self._expected_events(action["entitled_shares"])
        recorded = False
        for entry in ledger.journal:
            event = entry["event"]
            if event["data"].get("action_id") == self.spec.action_id:
                if event not in expected.values():
                    raise AdapterError("action event differs from pinned rights policy; tax mutations are unsupported")
                recorded |= event == expected["record"]
            elif (recorded and event["kind"] in {"buy_fill", "sell_fill"}
                  and event["data"]["symbol"] == self.spec.symbol
                  and _CA._time(event["effective_at"]).date().isoformat() == self.spec.record_date):
                raise AdapterError("fill after registration; rebuild the true record-date closing snapshot")
        if not recorded:
            raise AdapterError("registration journal missing")
        return action, expected

    def record_event(self, ledger):
        state = self._ledger(ledger, self._times["record_at"])
        if self.spec.action_id in state["actions"]:
            raise AdapterError("registration already exists; retry the saved event")
        quantity = sum(lot["quantity"] for lot in state["lots"].values() if lot["symbol"] == self.spec.symbol)
        if any(a["symbol"] == self.spec.symbol and a.get("bonus_pending", 0) for a in state["actions"].values()):
            raise UnsupportedCorporateAction("another pending distribution at registration requires entitlement coverage")
        return self._expected_events(quantity)["record"]

    def activation_event(self, ledger):
        _, events = self._action(ledger, self._times["activate_at"], active=False)
        return events["activate"]

    def cash_payment_event(self, ledger):
        action, events = self._action(ledger, self._times["cash_available_at"], active=True)
        if action["cash_paid"]:
            raise AdapterError("cash already paid; retry the saved event")
        return events["cash"]

    def listing_event(self, ledger):
        action, events = self._action(ledger, self._times["stock_listed_at"], active=True)
        if not action["bonus_pending"]:
            raise AdapterError("shares already listed; retry the saved event")
        return events["listed"]

    def require_tax_execution_ready(self):
        raise UnsupportedCorporateAction("stock distribution tax lineage remains unimplemented: " + "; ".join(TAX_GAPS))


def economic_share_positions(snapshot):
    """Include pending-only symbols in marks, without inventing sellable lots."""
    positions = {}
    for lot in snapshot["lots"].values():
        if lot["quantity"]:
            pos = positions.setdefault(lot["symbol"], {"listed": 0, "pending": 0})
            pos["listed"] += lot["quantity"]
    for action in snapshot["actions"].values():
        if action.get("active") and action["bonus_pending"]:
            pos = positions.setdefault(action["symbol"], {"listed": 0, "pending": 0})
            pos["pending"] += action["bonus_pending"]
    return {symbol: {**pos, "economic": pos["listed"] + pos["pending"]} for symbol, pos in positions.items()}


def physical_sale_allocations(ledger, symbol, quantity, *, as_of):
    """Book-time FIFO over actually sellable shares, explicitly not tax FIFO."""
    _CA._quantity(quantity)
    # sellable_quantity also validates the read time against the ledger head.
    if quantity > ledger.sellable_quantity(symbol, as_of=as_of):
        raise AdapterError("requested sale exceeds listed/T+1 sellable shares")
    result = {}
    for key, lot in sorted(ledger.snapshot()["lots"].items(), key=lambda pair: (_CA._time(pair[1]["booked_at"]), pair[0])):
        if lot["symbol"] != symbol or _CA._time(lot["sellable_at"]) > _CA._time(as_of):
            continue
        take = min(quantity, lot["quantity"])
        if take:
            result[key] = take
            quantity -= take
        if not quantity:
            return result
    raise AdapterError("sellable inventory reconciliation failed")

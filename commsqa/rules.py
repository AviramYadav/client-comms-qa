"""QA rules for client correspondence.

Each document is parsed back into fields (the same way a reviewer reads it),
then checked against the source system and against format / regulatory rules.
Every rule carries a severity and a root-cause category so defects can be
reported, trended and routed to the team that owns the fix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable

from .doc_templates import CLAUSES, CURRENT_VERSION, REQUIRED_CLAUSES
from .generate import mask_account, trade_amounts
from .money import add_business_days, d2, is_business_day, isin_valid, parse_amount

LINE_RE = re.compile(r"^([A-Za-z][A-Za-z ()/]*?)\s*:\s*(.*)$")


def extract(body: str) -> dict:
    """Parse 'Label : value' lines plus the address block into a dict of fields."""
    fields, lines = {}, body.splitlines()
    for i, line in enumerate(lines):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        key = re.sub(r"[^a-z]+", "_", m.group(1).lower()).strip("_")
        fields[key] = m.group(2).strip()
        if key == "to":
            fields["name"] = fields.pop("to")
            fields["address"] = lines[i + 1].strip()
            city, _, pin = lines[i + 2].strip().rpartition(" - ")
            fields["city"], fields["pin"] = city, pin
    if "security" in fields:
        m = re.match(r"(.*) \((\w+)\)$", fields["security"])
        if m:
            fields["security"], fields["isin"] = m.group(1), m.group(2)
    if "amount" in fields:
        currency, _, amount = fields["amount"].partition(" ")
        fields["currency"], fields["amount"] = currency, amount
    m = re.search(r"Template (\S+)", body)
    fields["template"] = m.group(1) if m else ""
    return fields


@dataclass(frozen=True)
class Rule:
    code: str
    title: str
    doc_types: tuple
    severity: str      # Critical | High | Medium | Low
    root_cause: str    # Data entry | Static data | System calculation | Template | Process
    description: str
    check: Callable    # (doc, fields, source) -> list of (field, expected, found)


def _diff(field, expected, found):
    return [(field, str(expected), str(found))] if str(expected) != str(found) else []


def _amount(text):
    try:
        return parse_amount(text)
    except (InvalidOperation, AttributeError):
        return None


def r_name(doc, f, s):
    return _diff("name", s["client"]["name"], f.get("name", ""))


def r_address(doc, f, s):
    c = s["client"]
    return _diff("address", f"{c['address']}, {c['city']} - {c['pin']}",
                 f"{f.get('address', '')}, {f.get('city', '')} - {f.get('pin', '')}")


def r_masking(doc, f, s):
    shown = f.get("account", "")
    if re.fullmatch(r"X+\d{4}", shown):
        return _diff("account", mask_account(s["account_no"]), shown)
    return [("account", mask_account(s["account_no"]), shown)]


def r_trade_amounts(doc, f, s):
    t = s["trade"]
    exp = trade_amounts(t["quantity"], Decimal(t["price"]), Decimal(t["brokerage"]), t["side"])
    out = []
    for key, label in [("gross", "gross_consideration_inr"), ("brokerage", "brokerage_inr"),
                       ("gst", "gst_on_brokerage_inr"), ("net", "net_amount_inr")]:
        out += _diff(key, exp[key], _amount(f.get(label, "")))
    out += _diff("quantity", t["quantity"], f.get("quantity", ""))
    out += _diff("price", d2(t["price"]), _amount(f.get("price_inr", "")))
    return out


def r_payment_amount(doc, f, s):
    return _diff("amount", d2(s["payment"]["amount"]), _amount(f.get("amount", "")))


def r_arithmetic(doc, f, s):
    """Internal consistency of the printed figures, independent of the source system."""
    q, p = Decimal(f.get("quantity", "0") or 0), _amount(f.get("price_inr", ""))
    gross, brk = _amount(f.get("gross_consideration_inr", "")), _amount(f.get("brokerage_inr", ""))
    gst, net = _amount(f.get("gst_on_brokerage_inr", "")), _amount(f.get("net_amount_inr", ""))
    if None in (p, gross, brk, gst, net):
        return [("amounts", "all amounts present", "unreadable amount")]
    out = _diff("gross = qty x price", d2(q * p), gross)
    out += _diff("gst = 18% of brokerage", d2(brk * Decimal("0.18")), gst)
    sign = 1 if f.get("transaction") == "BUY" else -1
    out += _diff("net = gross +/- charges", d2(gross + sign * (brk + gst)), net)
    return out


def r_settlement(doc, f, s):
    trade_date = date.fromisoformat(s["trade"]["trade_date"])
    return (_diff("trade_date", trade_date.isoformat(), f.get("trade_date", "")) +
            _diff("settlement_date (T+1)", add_business_days(trade_date, 1).isoformat(), f.get("settlement_date", "")))


def r_currency(doc, f, s):
    return _diff("currency", s["payment"]["currency"], f.get("currency", ""))


def r_value_date(doc, f, s):
    out = _diff("value_date", s["payment"]["value_date"], f.get("value_date", ""))
    try:
        if not is_business_day(date.fromisoformat(f.get("value_date", ""))):
            out.append(("value_date", "a business day", f"{f['value_date']} (non-business day)"))
    except ValueError:
        out.append(("value_date", "ISO date", f.get("value_date", "")))
    return out


def r_isin(doc, f, s):
    shown = f.get("isin", "")
    if not isin_valid(shown):
        return [("isin", s["trade"]["isin"], f"{shown} (check digit invalid)")]
    return _diff("isin", s["trade"]["isin"], shown)


def r_clauses(doc, f, s):
    return [("clause", CLAUSES[k][:60] + "...", "missing")
            for k in REQUIRED_CLAUSES[doc["doc_type"]] if CLAUSES[k] not in doc["body"]]


def r_template(doc, f, s):
    return _diff("template_version", CURRENT_VERSION[doc["doc_type"]], f.get("template", ""))


def r_notice(doc, f, s):
    n = s["notice"]
    issued = date.fromisoformat(doc["issued_on"])
    out = _diff("effective_date", n["effective_date"], f.get("effective_date", ""))
    effective = date.fromisoformat(n["effective_date"])
    if effective < issued + timedelta(days=30):
        out.append(("notice period", ">= 30 days", f"{(effective - issued).days} days"))
    return out


TC, PA, RN = ("TRADE_CONFIRM",), ("PAYMENT_ADVICE",), ("RATE_NOTICE",)
ALL = TC + PA + RN

RULES = [
    Rule("R01", "Client name matches master", ALL, "High", "Data entry",
         "Name printed on the document equals the client master record.", r_name),
    Rule("R02", "Address is current", ALL, "Medium", "Static data",
         "Address equals the current address, not one from address history.", r_address),
    Rule("R03", "Account number masked", ALL, "Critical", "System calculation",
         "Only the last four digits of the account may be printed.", r_masking),
    Rule("R04", "Trade figures reconcile to source", TC, "Critical", "Data entry",
         "Quantity, price, gross, brokerage, GST and net amount equal the booked trade.", r_trade_amounts),
    Rule("R05", "Payment amount reconciles to source", PA, "Critical", "Data entry",
         "Amount equals the payment instruction.", r_payment_amount),
    Rule("R06", "Printed trade figures add up", TC, "High", "System calculation",
         "gross = quantity x price, GST = 18% of brokerage, net = gross +/- charges.", r_arithmetic),
    Rule("R07", "Settlement date is T+1", TC, "High", "Process",
         "Trade date matches source and settlement is the next business day.", r_settlement),
    Rule("R08", "Currency matches source", PA, "Critical", "Data entry",
         "Currency code equals the payment instruction.", r_currency),
    Rule("R09", "Value date valid", PA, "Medium", "Process",
         "Value date equals the instruction and falls on a business day.", r_value_date),
    Rule("R10", "ISIN valid and correct", TC, "Medium", "Data entry",
         "ISIN passes its check digit and equals the traded security.", r_isin),
    Rule("R11", "Mandatory clauses present", ALL, "High", "Template",
         "All regulatory and service clauses for the document type are printed.", r_clauses),
    Rule("R12", "Current template version", ALL, "Low", "Template",
         "Document was produced from the current approved template.", r_template),
    Rule("R13", "30-day notice period respected", RN, "Critical", "Process",
         "Rate changes take effect no earlier than 30 days after the notice.", r_notice),
]
RULES_BY_CODE = {r.code: r for r in RULES}

# Which rule(s) should catch each planted defect - used to score the engine.
EXPECTED_RULES = {
    "NAME_MISSPELT": {"R01"}, "STALE_ADDRESS": {"R02"}, "UNMASKED_ACCOUNT": {"R03"},
    "AMOUNT_TRANSPOSED": {"R04", "R05"}, "GST_OLD_RATE": {"R04"}, "T2_SETTLEMENT": {"R07"},
    "BAD_ISIN": {"R10"}, "OLD_TEMPLATE": {"R11"}, "WRONG_CURRENCY": {"R08"},
    "WEEKEND_VALUE_DATE": {"R09"}, "SHORT_NOTICE": {"R13"},
}

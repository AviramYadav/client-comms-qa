"""Builds eight weeks of synthetic source data and the client documents printed from it.

A small share of documents get realistic defects. Two of them are "incidents"
with a start and an end, so the weekly trend has a story to tell:
  * weeks 1-4: one branch still prints trade confirmations from the old
    template, which is missing the 24-hour discrepancy clause (fixed in week 5);
  * week 6: a configuration release computes GST on brokerage at 15% instead
    of 18% for two days.
Every planted defect is recorded so the QA engine can be scored.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from .doc_templates import CURRENT_VERSION, render
from .money import add_business_days, d2, fmt_inr, is_business_day, isin_check_digit

FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Rahul", "Vikram", "Sanjay", "Amit", "Priya",
         "Ananya", "Diya", "Isha", "Kavya", "Neha", "Pooja", "Riya", "Sneha", "Meera", "Imran", "Fatima",
         "Gurpreet", "Harish", "Lakshmi", "Nikhil", "Swati", "Tarun", "Varun", "Yash"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Patel", "Shah", "Mehta", "Joshi", "Reddy", "Nair",
        "Iyer", "Rao", "Das", "Bose", "Khan", "Shaikh", "Gill", "Menon", "Kulkarni", "Deshmukh", "Mishra"]
STREETS = ["MG Road", "Station Road", "Link Road", "Nehru Nagar", "Park Street", "Civil Lines",
           "Lake View Colony", "Shivaji Nagar", "Sector 14", "Residency Road"]
CITIES = {"Mumbai": "400", "Delhi": "110", "Bengaluru": "560", "Pune": "411", "Chennai": "600",
          "Hyderabad": "500", "Kolkata": "700", "Jaipur": "302"}
ISSUERS = ["Meridian Power", "Trident Steel", "Saffron Foods", "Orion Infotech", "Lotus Pharma", "Indus Cement",
           "Granite Realty", "Kestrel Motors", "Bluestone Finance", "Everest Chemicals", "Harbor Logistics",
           "Silverline Telecom", "Crescent Textiles", "Vertex Energy", "Sapphire Retail", "Northwind Airlines"]
PURPOSES = ["Rent", "School fees", "Invoice settlement", "Family maintenance", "Loan EMI", "Insurance premium"]

DOC_MIX = [("TRADE_CONFIRM", 0.60), ("PAYMENT_ADVICE", 0.25), ("RATE_NOTICE", 0.15)]
START = date(2026, 8, 3)  # a Monday
GST_RATE, OLD_GST_RATE = Decimal("0.18"), Decimal("0.15")

# Background defect rates per document type (independent of the two incidents).
BASE_RATES = {
    "TRADE_CONFIRM": {"NAME_MISSPELT": 0.006, "STALE_ADDRESS": 0.015, "UNMASKED_ACCOUNT": 0.003,
                      "AMOUNT_TRANSPOSED": 0.004, "T2_SETTLEMENT": 0.004, "BAD_ISIN": 0.004},
    "PAYMENT_ADVICE": {"NAME_MISSPELT": 0.006, "STALE_ADDRESS": 0.015, "UNMASKED_ACCOUNT": 0.004,
                       "AMOUNT_TRANSPOSED": 0.006, "WRONG_CURRENCY": 0.003, "WEEKEND_VALUE_DATE": 0.004},
    "RATE_NOTICE": {"NAME_MISSPELT": 0.006, "STALE_ADDRESS": 0.015, "SHORT_NOTICE": 0.006},
}


def trade_amounts(quantity: int, price: Decimal, brokerage: Decimal, side: str, gst_rate=GST_RATE) -> dict:
    gross = d2(price * quantity)
    gst = d2(brokerage * gst_rate)
    net = gross + brokerage + gst if side == "BUY" else gross - brokerage - gst
    return {"gross": gross, "brokerage": d2(brokerage), "gst": gst, "net": d2(net)}


def mask_account(account_no: str) -> str:
    return "X" * (len(account_no) - 4) + account_no[-4:]


def _transpose_digits(r: random.Random, value: Decimal) -> Decimal:
    s = f"{value:.2f}"
    spots = [i for i in range(len(s) - 1) if s[i].isdigit() and s[i + 1].isdigit() and s[i] != s[i + 1]]
    if not spots:
        return value + 10
    i = r.choice(spots)
    return Decimal(s[:i] + s[i + 1] + s[i] + s[i + 2:])


def _misspell(r: random.Random, name: str) -> str:
    tokens = name.split()
    i = r.randrange(len(tokens))
    t = tokens[i]
    spots = [j for j in range(1, len(t) - 1) if t[j] != t[j + 1]]
    if spots:
        j = r.choice(spots)
        tokens[i] = t[:j] + t[j + 1] + t[j] + t[j + 2:]
    return " ".join(tokens)


def _week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def generate(conn, seed: int = 5, weeks: int = 8, docs_per_day: int = 60) -> dict:
    r = random.Random(seed)
    clients, history = [], {}
    for i in range(1, 601):
        city = r.choice(list(CITIES))
        c = {"client_id": f"CL{i:05d}", "name": f"{r.choice(FIRST)} {r.choice(LAST)}",
             "address": f"{r.randint(1, 400)}, {r.choice(STREETS)}", "city": city,
             "pin": CITIES[city] + f"{r.randint(1, 99):03d}"}
        clients.append(c)
        if r.random() < 0.2:  # moved house before the period; the old address is still on file
            old_city = r.choice(list(CITIES))
            history[c["client_id"]] = {"address": f"{r.randint(1, 400)}, {r.choice(STREETS)}", "city": old_city,
                                       "pin": CITIES[old_city] + f"{r.randint(1, 99):03d}"}
    conn.executemany("INSERT INTO clients VALUES (:client_id, :name, :address, :city, :pin)", clients)
    conn.executemany("INSERT INTO address_history VALUES (?, ?, ?, ?, ?)",
                     [(cid, h["address"], h["city"], h["pin"], (START - timedelta(days=r.randint(30, 400))).isoformat())
                      for cid, h in history.items()])

    accounts = {}
    for c in clients:
        demat = f"1203{r.randint(10**9, 10**10 - 1)}"[:14]
        savings = f"{r.randint(10**11, 10**12 - 1)}"
        accounts[c["client_id"]] = {"DEMAT": demat, "SAVINGS": savings}
        conn.execute("INSERT INTO accounts VALUES (?, ?, 'Demat account', 'INR')", (demat, c["client_id"]))
        conn.execute("INSERT INTO accounts VALUES (?, ?, 'Savings account', 'INR')", (savings, c["client_id"]))

    securities = []
    for n, issuer in enumerate(ISSUERS, start=101):
        first11 = f"INE{n:03d}{r.choice('ABCDEFGH')}01{r.randint(0, 2)}{r.randint(0, 9)}"  # 11 chars + check digit
        securities.append((first11 + isin_check_digit(first11), f"{issuer} Ltd"))
    conn.executemany("INSERT INTO securities VALUES (?, ?)", securities)

    by_id = {c["client_id"]: c for c in clients}
    seq, planted_count = 0, 0
    day = START
    end = START + timedelta(weeks=weeks)
    while day < end:
        if not is_business_day(day):
            day += timedelta(days=1)
            continue
        week_no = (day - START).days // 7 + 1
        for _ in range(r.randint(docs_per_day - 10, docs_per_day + 10)):
            seq += 1
            doc_type = r.choices([t for t, _ in DOC_MIX], weights=[w for _, w in DOC_MIX])[0]
            client = by_id[r.choice(clients)["client_id"]]
            codes = [k for k, p in BASE_RATES[doc_type].items() if r.random() < p]
            if doc_type == "TRADE_CONFIRM" and week_no <= 4 and r.random() < 0.06:
                codes.append("OLD_TEMPLATE")
            if doc_type == "TRADE_CONFIRM" and week_no == 6 and day.weekday() in (1, 2) and r.random() < 0.25:
                codes.append("GST_OLD_RATE")
            if "STALE_ADDRESS" in codes and client["client_id"] not in history:
                codes.remove("STALE_ADDRESS")
            planted_count += bool(codes)
            _issue(conn, r, seq, doc_type, day, client, accounts[client["client_id"]], history, securities, codes)
        day += timedelta(days=1)
    conn.commit()
    return {"documents": seq, "documents_with_planted_defects": planted_count}


def _issue(conn, r, seq, doc_type, day, client, accts, history, securities, codes) -> None:
    doc_id, ref = f"D{seq:06d}", f"{doc_type[:2]}{day:%y%m%d}{seq:06d}"
    addr = history[client["client_id"]] if "STALE_ADDRESS" in codes else client
    fields = {"ref": ref, "issued_on": day.isoformat(), "name": client["name"], "address": addr["address"],
              "city": addr["city"], "pin": addr["pin"]}
    if "NAME_MISSPELT" in codes:
        fields["name"] = _misspell(r, client["name"])
    version = CURRENT_VERSION[doc_type]

    if doc_type == "TRADE_CONFIRM":
        account = accts["DEMAT"]
        isin, security = r.choice(securities)
        side, qty = r.choice(["BUY", "SELL"]), r.randint(1, 500)
        price = d2(Decimal(r.randint(1000, 80000)) * Decimal("0.05"))
        brokerage = max(Decimal("20.00"), d2(price * qty * Decimal("0.0003")))
        conn.execute("INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (ref, account, day.isoformat(), side, isin, qty, str(price), str(brokerage)))
        amounts = trade_amounts(qty, price, brokerage, side,
                                OLD_GST_RATE if "GST_OLD_RATE" in codes else GST_RATE)
        if "AMOUNT_TRANSPOSED" in codes:
            amounts["net"] = _transpose_digits(r, amounts["net"])
        settle = add_business_days(day, 2 if "T2_SETTLEMENT" in codes else 1)
        shown_isin = isin[:-1] + str((int(isin[-1]) + r.randint(1, 9)) % 10) if "BAD_ISIN" in codes else isin
        fields.update(trade_date=day.isoformat(), settlement_date=settle.isoformat(), side=side,
                      security=security, isin=shown_isin, quantity=qty, price=fmt_inr(price),
                      **{k: fmt_inr(v) for k, v in amounts.items()})
        if "OLD_TEMPLATE" in codes:
            version = "TC-v2.9"
    elif doc_type == "PAYMENT_ADVICE":
        account = accts["SAVINGS"]
        amount = d2(Decimal(round(r.lognormvariate(9.5, 1.1), 2)))
        value_date = day
        if "WEEKEND_VALUE_DATE" in codes:  # booked for the coming Saturday by a manual override
            value_date = day + timedelta(days=5 - day.weekday())
        conn.execute("INSERT INTO payments VALUES (?, ?, ?, ?, 'INR', ?, ?)",
                     (ref, account, value_date.isoformat(), str(amount),
                      f"{r.choice(FIRST)} {r.choice(LAST)}", r.choice(PURPOSES)))
        row = conn.execute("SELECT beneficiary, purpose FROM payments WHERE ref = ?", (ref,)).fetchone()
        shown = _transpose_digits(r, amount) if "AMOUNT_TRANSPOSED" in codes else amount
        fields.update(value_date=value_date.isoformat(), amount=fmt_inr(shown),
                      currency="USD" if "WRONG_CURRENCY" in codes else "INR",
                      beneficiary=row["beneficiary"], purpose=row["purpose"])
    else:
        account = accts["SAVINGS"]
        old = Decimal(r.choice(["3.00", "3.25", "3.50", "4.00"]))
        new = old + Decimal(r.choice(["-0.25", "-0.50", "0.25"]))
        notice_days = r.randint(12, 20) if "SHORT_NOTICE" in codes else r.randint(30, 45)
        effective = day + timedelta(days=notice_days)
        conn.execute("INSERT INTO rate_notices VALUES (?, ?, 'Savings account', ?, ?, ?)",
                     (ref, account, str(old), str(new), effective.isoformat()))
        fields.update(product="Savings account", old_rate=f"{old:.2f}", new_rate=f"{new:.2f}",
                      effective_date=effective.isoformat())

    fields["account"] = account if "UNMASKED_ACCOUNT" in codes else mask_account(account)
    conn.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (doc_id, doc_type, ref, client["client_id"], day.isoformat(), _week_label(day), version,
                  render(version, fields)))
    conn.executemany("INSERT INTO planted_defects VALUES (?, ?)", [(doc_id, c) for c in codes])

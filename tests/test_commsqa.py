from datetime import date
from decimal import Decimal

import pytest

from commsqa import db, qa
from commsqa.doc_templates import render
from commsqa.generate import generate, mask_account, trade_amounts
from commsqa.money import add_business_days, fmt_inr, isin_valid, parse_amount
from commsqa.rules import extract


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    c = db.connect(tmp_path_factory.mktemp("qa") / "t.db")
    generate(c, seed=5, weeks=8)
    qa.run(c)
    return c


# ---- helpers ---------------------------------------------------------------
def test_indian_number_format_round_trips():
    assert fmt_inr(12345678.5) == "1,23,45,678.50"
    assert fmt_inr(999) == "999.00"
    assert parse_amount("1,23,45,678.50") == Decimal("12345678.50")


def test_isin_check_digit_on_published_isins():
    assert isin_valid("US0378331005")
    assert isin_valid("INE002A01018")
    assert not isin_valid("INE002A01019")


def test_t_plus_one_skips_weekends_and_holidays():
    assert add_business_days(date(2026, 8, 7), 1) == date(2026, 8, 10)     # Friday -> Monday
    assert add_business_days(date(2026, 10, 1), 1) == date(2026, 10, 5)    # Thu -> skips 2 Oct holiday + weekend


def test_trade_amounts_buy_and_sell():
    buy = trade_amounts(100, Decimal("250.00"), Decimal("20.00"), "BUY")
    assert buy == {"gross": Decimal("25000.00"), "brokerage": Decimal("20.00"),
                   "gst": Decimal("3.60"), "net": Decimal("25023.60")}
    assert trade_amounts(100, Decimal("250.00"), Decimal("20.00"), "SELL")["net"] == Decimal("24976.40")


def test_extract_reads_back_what_render_wrote():
    body = render("PA-v2.0", dict(ref="PA1", issued_on="2026-08-03", name="Neha Rao", address="1, MG Road",
                                  city="Pune", pin="411001", account=mask_account("123456789012"),
                                  value_date="2026-08-03", currency="INR", amount="1,500.00",
                                  beneficiary="Amit Shah", purpose="Rent"))
    f = extract(body)
    assert (f["name"], f["address"], f["city"], f["pin"]) == ("Neha Rao", "1, MG Road", "Pune", "411001")
    assert (f["currency"], f["amount"], f["account"], f["template"]) == ("INR", "1,500.00", "XXXXXXXX9012", "PA-v2.0")


# ---- engine against seeded defects ----------------------------------------
def test_every_seeded_defect_is_caught_with_no_false_alarms(conn):
    result = qa.self_test(conn, qa.latest_run_id(conn))
    assert result["recall"] == 1.0, result["by_code"]
    assert result["clean_docs_flagged"] == 0


def test_template_incident_ends_after_week_four(conn):
    rid = qa.latest_run_id(conn)
    by_week = {}
    for r in conn.execute("SELECT d.week, COUNT(*) n FROM defects x JOIN documents d USING (doc_id) "
                          "WHERE x.run_id = ? AND x.rule = 'R11' GROUP BY d.week", (rid,)):
        by_week[r["week"]] = r["n"]
    weeks = sorted(w for w in {r[0] for r in conn.execute("SELECT DISTINCT week FROM documents")})
    assert all(by_week.get(w, 0) > 0 for w in weeks[:4])
    assert all(by_week.get(w, 0) == 0 for w in weeks[4:])


def test_review_decisions_survive_a_rerun(conn):
    rid = qa.latest_run_id(conn)
    before = qa.kpis(conn, rid)["defects"]
    d = conn.execute("SELECT * FROM defects WHERE run_id = ? LIMIT 1", (rid,)).fetchone()
    conn.execute("UPDATE defects SET status='REJECTED', reviewer='qa1', comment='x' WHERE defect_id = ?", (d["defect_id"],))
    conn.commit()
    new = qa.run(conn)["run_id"]
    again = conn.execute("SELECT status, reviewer FROM defects WHERE run_id = ? AND doc_id = ? AND rule = ? AND field = ?",
                         (new, d["doc_id"], d["rule"], d["field"])).fetchone()
    assert tuple(again) == ("REJECTED", "qa1")
    # rejected defects drop out of accuracy figures
    assert qa.kpis(conn, new)["defects"] == before - 1


def test_sample_size_matches_textbook_values():
    assert qa.sample_size(1_000_000, 0.05, 0.05, 0.95) == 73    # 1.96^2 * .05 * .95 / .05^2 = 72.99
    assert qa.sample_size(300, 0.04, 0.02, 0.95) == 166         # n0 = 368.8, corrected for N = 300 -> 165.7
    assert qa.sample_size(10, 0.5, 0.01, 0.99) == 10

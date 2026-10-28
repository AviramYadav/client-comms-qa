"""Runs every applicable rule over every document and stores the defects.

Re-running keeps analysts' review decisions: a defect with the same
(document, rule, field) inherits the status and comment from the last run.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from datetime import datetime

from .rules import EXPECTED_RULES, RULES, RULES_BY_CODE, extract


def _sources(conn) -> dict:
    q = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
    return {
        "clients": {r["client_id"]: r for r in q("SELECT * FROM clients")},
        "trades": {r["ref"]: r for r in q("SELECT * FROM trades")},
        "payments": {r["ref"]: r for r in q("SELECT * FROM payments")},
        "notices": {r["ref"]: r for r in q("SELECT * FROM rate_notices")},
    }


def source_for(doc: dict, src: dict) -> dict:
    s = {"client": src["clients"][doc["client_id"]]}
    ref = doc["source_ref"]
    if doc["doc_type"] == "TRADE_CONFIRM":
        s["trade"] = src["trades"][ref]
        s["account_no"] = s["trade"]["account_no"]
    elif doc["doc_type"] == "PAYMENT_ADVICE":
        s["payment"] = src["payments"][ref]
        s["account_no"] = s["payment"]["account_no"]
    else:
        s["notice"] = src["notices"][ref]
        s["account_no"] = s["notice"]["account_no"]
    return s


def check_document(doc: dict, source: dict) -> list[dict]:
    fields = extract(doc["body"])
    found = []
    for rule in RULES:
        if doc["doc_type"] in rule.doc_types:
            for field, expected, actual in rule.check(doc, fields, source):
                found.append({"doc_id": doc["doc_id"], "rule": rule.code, "severity": rule.severity,
                              "root_cause": rule.root_cause, "field": field, "expected": expected, "found": actual})
    return found


def latest_run_id(conn):
    row = conn.execute("SELECT MAX(run_id) FROM qa_runs").fetchone()
    return row[0]


def run(conn) -> dict:
    t0 = time.perf_counter()
    previous = latest_run_id(conn)
    carried = {}
    if previous:
        for r in conn.execute("SELECT * FROM defects WHERE run_id = ? AND status != 'OPEN'", (previous,)):
            carried[(r["doc_id"], r["rule"], r["field"])] = (r["status"], r["reviewer"], r["comment"], r["reviewed_at"])

    src = _sources(conn)
    docs = [dict(d) for d in conn.execute("SELECT * FROM documents ORDER BY doc_id")]
    defects = [d for doc in docs for d in check_document(doc, source_for(doc, src))]

    cur = conn.execute("INSERT INTO qa_runs (started_at, documents, defects, seconds) VALUES (?, ?, ?, 0)",
                       (datetime.now().isoformat(timespec="seconds"), len(docs), len(defects)))
    run_id = cur.lastrowid
    rows = []
    for d in defects:
        status, reviewer, comment, reviewed_at = carried.get((d["doc_id"], d["rule"], d["field"]), ("OPEN", None, None, None))
        rows.append((run_id, d["doc_id"], d["rule"], d["severity"], d["root_cause"], d["field"],
                     d["expected"], d["found"], status, reviewer, comment, reviewed_at))
    conn.executemany("INSERT INTO defects (run_id, doc_id, rule, severity, root_cause, field, expected, found, "
                     "status, reviewer, comment, reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    seconds = round(time.perf_counter() - t0, 2)
    conn.execute("UPDATE qa_runs SET seconds = ? WHERE run_id = ?", (seconds, run_id))
    conn.commit()
    return {"run_id": run_id, "documents": len(docs), "defects": len(defects), "seconds": seconds}


def self_test(conn, run_id) -> dict:
    """Score the latest run against the defects the generator planted."""
    planted = {}
    for r in conn.execute("SELECT doc_id, code FROM planted_defects"):
        planted.setdefault(r["doc_id"], set()).add(r["code"])
    flagged = {}
    for r in conn.execute("SELECT doc_id, rule FROM defects WHERE run_id = ?", (run_id,)):
        flagged.setdefault(r["doc_id"], set()).add(r["rule"])

    total, caught = Counter(), Counter()
    for doc_id, codes in planted.items():
        for code in codes:
            total[code] += 1
            caught[code] += bool(EXPECTED_RULES[code] & flagged.get(doc_id, set()))
    clean_flagged = [d for d in flagged if d not in planted]
    return {
        "by_code": [{"defect": c, "planted": total[c], "caught": caught[c]} for c in sorted(total)],
        "recall": sum(caught.values()) / max(1, sum(total.values())),
        "docs_with_planted": len(planted),
        "clean_docs_flagged": len(clean_flagged),
    }


# ---------------------------------------------------------------- MI queries
ACTIVE = "status != 'REJECTED'"


def kpis(conn, run_id) -> dict:
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    docs = one("SELECT COUNT(*) FROM documents")
    defective = one(f"SELECT COUNT(DISTINCT doc_id) FROM defects WHERE run_id = ? AND {ACTIVE}", run_id)
    return {
        "documents": docs,
        "defective_documents": defective,
        "accuracy": 1 - defective / docs if docs else 1.0,
        "defects": one(f"SELECT COUNT(*) FROM defects WHERE run_id = ? AND {ACTIVE}", run_id),
        "critical": one(f"SELECT COUNT(*) FROM defects WHERE run_id = ? AND {ACTIVE} AND severity = 'Critical'", run_id),
        "open": one("SELECT COUNT(*) FROM defects WHERE run_id = ? AND status = 'OPEN'", run_id),
    }


def weekly(conn, run_id) -> list[dict]:
    rows = conn.execute(f"""
        SELECT d.week,
               COUNT(*)                                   AS documents,
               COUNT(x.doc_id)                            AS defective,
               COALESCE(SUM(x.n), 0)                      AS defects,
               COALESCE(SUM(x.critical), 0)               AS critical
        FROM documents d
        LEFT JOIN (SELECT doc_id, COUNT(*) n, SUM(severity = 'Critical') critical
                   FROM defects WHERE run_id = ? AND {ACTIVE} GROUP BY doc_id) x ON x.doc_id = d.doc_id
        GROUP BY d.week ORDER BY d.week""", (run_id,))
    out = []
    for r in rows:
        r = dict(r)
        r["accuracy"] = 1 - r["defective"] / r["documents"]
        out.append(r)
    return out


def pareto(conn, run_id) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        f"SELECT rule, COUNT(*) n FROM defects WHERE run_id = ? AND {ACTIVE} GROUP BY rule ORDER BY n DESC", (run_id,))]
    total, running = sum(r["n"] for r in rows) or 1, 0
    for r in rows:
        running += r["n"]
        rule = RULES_BY_CODE[r["rule"]]
        r.update(title=rule.title, severity=rule.severity, root_cause=rule.root_cause,
                 share=r["n"] / total, cumulative=running / total)
    return rows


def breakdown(conn, run_id, column: str) -> list[dict]:
    assert column in {"root_cause", "severity"}
    return [dict(r) for r in conn.execute(
        f"SELECT {column} AS label, COUNT(*) n, COUNT(DISTINCT doc_id) docs FROM defects "
        f"WHERE run_id = ? AND {ACTIVE} GROUP BY {column} ORDER BY n DESC", (run_id,))]


def by_doc_type(conn, run_id) -> list[dict]:
    rows = conn.execute(f"""
        SELECT d.doc_type, COUNT(*) documents, COUNT(x.doc_id) defective
        FROM documents d
        LEFT JOIN (SELECT DISTINCT doc_id FROM defects WHERE run_id = ? AND {ACTIVE}) x ON x.doc_id = d.doc_id
        GROUP BY d.doc_type ORDER BY d.doc_type""", (run_id,))
    return [dict(r, accuracy=1 - r["defective"] / r["documents"]) for r in rows]


# ---------------------------------------------------------------- sampling
Z = {0.90: 1.645, 0.95: 1.960, 0.99: 2.576}


def sample_size(population: int, expected_rate: float, margin: float, confidence: float = 0.95) -> int:
    """Attribute-sampling size with finite population correction."""
    n0 = Z[confidence] ** 2 * expected_rate * (1 - expected_rate) / margin ** 2
    return min(population, math.ceil(n0 / (1 + (n0 - 1) / population)))

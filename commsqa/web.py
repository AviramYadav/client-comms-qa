"""Flask app: QA dashboard, defect log, document review and sampling calculator."""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from markupsafe import Markup, escape

from . import db, qa
from .rules import RULES, RULES_BY_CODE, extract
from .scorecard import build

# Which printed line a defect field points at, so the document view can highlight it.
FIELD_LINES = {
    "name": "To :", "account": "Account", "gross": "Gross", "brokerage": "Brokerage", "gst": "GST",
    "net": "Net amount", "quantity": "Quantity", "price": "Price", "gross = qty x price": "Gross",
    "gst = 18% of brokerage": "GST", "net = gross +/- charges": "Net amount", "trade_date": "Trade date",
    "settlement_date (T+1)": "Settlement", "currency": "Amount", "amount": "Amount", "value_date": "Value date",
    "isin": "Security", "template_version": "Template ", "effective_date": "Effective", "notice period": "Effective",
}
SEVERITY_ORDER = ["Critical", "High", "Medium", "Low"]


def trend_svg(weeks: list[dict]) -> Markup:
    """Small hand-built SVG: bars = defective documents, line = accuracy."""
    if not weeks:
        return Markup("")
    w, h, pad_l, pad_b, pad_t = 640, 200, 34, 26, 12
    plot_w, plot_h = w - pad_l - 40, h - pad_b - pad_t
    step = plot_w / len(weeks)
    peak = max(x["defective"] for x in weeks) or 1
    lo = min(x["accuracy"] for x in weeks)
    lo = min(0.9, lo - 0.01)
    parts = [f'<svg viewBox="0 0 {w} {h}" class="trend" role="img" aria-label="Weekly defective documents and accuracy">']
    for frac in (0, 0.5, 1):
        y = pad_t + plot_h * (1 - frac)
        parts.append(f'<line x1="{pad_l}" x2="{pad_l + plot_w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                     f'<text x="{pad_l - 6}" y="{y + 4:.1f}" class="ax" text-anchor="end">{round(peak * frac)}</text>'
                     f'<text x="{pad_l + plot_w + 6}" y="{y + 4:.1f}" class="ax">{lo + (1 - lo) * frac:.0%}</text>')
    points = []
    for i, x in enumerate(weeks):
        bx = pad_l + i * step + step * 0.2
        bh = plot_h * x["defective"] / peak
        parts.append(f'<rect x="{bx:.1f}" y="{pad_t + plot_h - bh:.1f}" width="{step * 0.6:.1f}" height="{bh:.1f}" class="bar">'
                     f'<title>{escape(x["week"])}: {x["defective"]} defective of {x["documents"]}</title></rect>'
                     f'<text x="{bx + step * 0.3:.1f}" y="{h - 8}" class="ax" text-anchor="middle">{escape(x["week"][-3:])}</text>')
        points.append(f'{bx + step * 0.3:.1f},{pad_t + plot_h * (1 - (x["accuracy"] - lo) / (1 - lo)):.1f}')
    parts.append(f'<polyline points="{" ".join(points)}" class="acc"/>')
    parts += [f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="2.5" class="dot"/>' for p in points]
    parts.append("</svg>")
    return Markup("".join(parts))


def highlight(body: str, defects: list[dict]) -> list[dict]:
    lines = body.splitlines()
    flagged: dict[int, list[str]] = {}
    for d in defects:
        if d["field"] == "address":
            idx = next((i for i, ln in enumerate(lines) if ln.startswith("To :")), None)
            targets = [idx + 1, idx + 2] if idx is not None else []
        else:
            prefix = FIELD_LINES.get(d["field"])
            targets = [i for i, ln in enumerate(lines) if prefix and ln.startswith(prefix)][:1]
        for t in targets:
            flagged.setdefault(t, []).append(d["rule"])
    return [{"text": ln, "rules": flagged.get(i, [])} for i, ln in enumerate(lines)]


def create_app(db_path: str | Path) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("COMMSQA_SECRET_KEY", "dev-only-change-me")
    conn = db.connect(db_path)

    def run_id():
        rid = qa.latest_run_id(conn)
        if rid is None:
            rid = qa.run(conn)["run_id"]
        return rid

    @app.context_processor
    def inject():
        return {"user": session.get("user", ""), "rules_by_code": RULES_BY_CODE}

    @app.post("/whoami")
    def whoami():
        session["user"] = request.form.get("user", "").strip()[:40]
        return redirect(request.referrer or url_for("dashboard"))

    @app.get("/")
    def dashboard():
        rid = run_id()
        weeks = qa.weekly(conn, rid)
        last = dict(conn.execute("SELECT * FROM qa_runs WHERE run_id = ?", (rid,)).fetchone())
        return render_template("dashboard.html", k=qa.kpis(conn, rid), weeks=weeks, chart=trend_svg(weeks),
                               pareto=qa.pareto(conn, rid)[:8], causes=qa.breakdown(conn, rid, "root_cause"),
                               types=qa.by_doc_type(conn, rid), last=last)

    @app.post("/run")
    def rerun():
        res = qa.run(conn)
        flash(f"QA run {res['run_id']} checked {res['documents']:,} documents in {res['seconds']}s "
              f"and found {res['defects']} defects. Earlier review decisions were carried over.", "ok")
        return redirect(url_for("dashboard"))

    @app.get("/scorecard.xlsx")
    def scorecard():
        buf = io.BytesIO()
        build(conn, buf)
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name=f"qa_scorecard_{datetime.now():%Y%m%d}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/defects")
    def defects():
        rid = run_id()
        filters = {k: request.args.get(k, "") for k in ["severity", "root_cause", "rule", "status", "week", "doc_type"]}
        sql = ("SELECT x.*, d.doc_type, d.week, d.issued_on FROM defects x JOIN documents d ON d.doc_id = x.doc_id "
               "WHERE x.run_id = ?")
        params = [rid]
        for k, v in filters.items():
            if v:
                sql += f" AND {'d' if k in ('week', 'doc_type') else 'x'}.{k} = ?"
                params.append(v)
        sql += (" ORDER BY CASE x.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,"
                " d.issued_on DESC LIMIT 1000")
        rows = [dict(r) for r in conn.execute(sql, params)]
        options = {
            "week": [r[0] for r in conn.execute("SELECT DISTINCT week FROM documents ORDER BY week")],
            "doc_type": ["TRADE_CONFIRM", "PAYMENT_ADVICE", "RATE_NOTICE"],
            "severity": SEVERITY_ORDER,
            "root_cause": sorted({r.root_cause for r in RULES}),
            "rule": [r.code for r in RULES],
            "status": ["OPEN", "CONFIRMED", "REJECTED"],
        }
        return render_template("defects.html", rows=rows, filters=filters, options=options)

    @app.get("/documents")
    def find_document():
        q = request.args.get("q", "").strip()
        row = conn.execute("SELECT doc_id FROM documents WHERE doc_id = ? OR source_ref = ?", (q.upper(), q.upper())).fetchone()
        if row:
            return redirect(url_for("document", doc_id=row["doc_id"]))
        flash(f"No document with ID or reference '{q}'.", "error")
        return redirect(request.referrer or url_for("dashboard"))

    @app.get("/documents/<doc_id>")
    def document(doc_id):
        doc = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        if doc is None:
            abort(404)
        doc = dict(doc)
        found = [dict(r) for r in conn.execute("SELECT * FROM defects WHERE run_id = ? AND doc_id = ? ORDER BY defect_id",
                                               (run_id(), doc_id))]
        src = qa.source_for(doc, qa._sources(conn))
        record = src.get("trade") or src.get("payment") or src.get("notice")
        neighbours = conn.execute(
            "SELECT (SELECT MAX(doc_id) FROM defects WHERE run_id = ? AND doc_id < ?),"
            "       (SELECT MIN(doc_id) FROM defects WHERE run_id = ? AND doc_id > ?)",
            (run_id(), doc_id, run_id(), doc_id)).fetchone()
        return render_template("document.html", doc=doc, lines=highlight(doc["body"], found), defects=found,
                               client=src["client"], record=record, parsed=extract(doc["body"]),
                               prev_id=neighbours[0], next_id=neighbours[1])

    @app.post("/defects/<int:defect_id>/review")
    def review(defect_id):
        user = session.get("user")
        row = conn.execute("SELECT doc_id FROM defects WHERE defect_id = ?", (defect_id,)).fetchone()
        if row is None:
            abort(404)
        if not user:
            flash("Enter your name in the sidebar before reviewing.", "error")
        elif request.form.get("status") not in ("CONFIRMED", "REJECTED", "OPEN"):
            flash("Unknown review outcome.", "error")
        else:
            conn.execute("UPDATE defects SET status = ?, reviewer = ?, comment = ?, reviewed_at = ? WHERE defect_id = ?",
                         (request.form["status"], user, request.form.get("comment", "").strip(),
                          datetime.now().isoformat(timespec="seconds"), defect_id))
            conn.commit()
            flash(f"Defect {defect_id} marked {request.form['status'].lower()}.", "ok")
        return redirect(url_for("document", doc_id=row["doc_id"]))

    @app.get("/rules")
    def rules():
        rid = run_id()
        counts = {r["rule"]: r["n"] for r in qa.pareto(conn, rid)}
        return render_template("rules.html", rules=RULES, counts=counts)

    @app.route("/sampling", methods=["GET", "POST"])
    def sampling():
        rid = run_id()
        weeks = qa.weekly(conn, rid)
        latest = weeks[-1] if weeks else {"documents": 0, "defective": 0, "week": ""}
        f = request.form
        population = int(f.get("population") or latest["documents"] or 1)
        rate = float(f.get("rate") or 4) / 100
        margin = float(f.get("margin") or 2) / 100
        confidence = float(f.get("confidence") or 0.95)
        n = qa.sample_size(population, rate, margin, confidence) if 0 < rate < 1 and margin > 0 else 0
        return render_template("sampling.html", population=population, rate=rate, margin=margin,
                               confidence=confidence, n=n, latest=latest, weeks=weeks)

    return app

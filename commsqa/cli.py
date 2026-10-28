"""Command line: generate sample data, run QA, or start the web app."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import db, qa
from .generate import generate
from .scorecard import build

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "comms.db"


def ensure_data(conn, seed: int = 5) -> None:
    if conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
        generate(conn, seed=seed)
        qa.run(conn)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m commsqa", description="Client correspondence QA")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="rebuild the sample source data and documents")
    g.add_argument("--seed", type=int, default=5)
    g.add_argument("--weeks", type=int, default=8)
    r = sub.add_parser("run", help="run all QA rules and write the Excel scorecard")
    r.add_argument("--out", default=str(ROOT / "reports" / "qa_scorecard.xlsx"))
    s = sub.add_parser("serve", help="start the web app")
    s.add_argument("--port", type=int, default=5002)
    args = p.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.db)
    if args.cmd == "generate":
        db.reset(conn)
        info = generate(conn, seed=args.seed, weeks=args.weeks)
        print(f"Generated {info['documents']:,} documents "
              f"({info['documents_with_planted_defects']} with seeded defects) into {args.db}")
    elif args.cmd == "run":
        ensure_data(conn)
        res = qa.run(conn)
        k = qa.kpis(conn, res["run_id"])
        print(f"Run {res['run_id']}: {res['documents']:,} documents, {res['defects']} defects in {res['seconds']}s")
        print(f"Accuracy {k['accuracy']:.2%} | defective documents {k['defective_documents']} | critical {k['critical']}")
        print("\nWeek        docs  defective  accuracy")
        for w in qa.weekly(conn, res["run_id"]):
            print(f"{w['week']}  {w['documents']:>5}  {w['defective']:>9}  {w['accuracy']:>8.2%}")
        t = qa.self_test(conn, res["run_id"])
        print("\nSelf-test against seeded defects:")
        for row in t["by_code"]:
            print(f"  {row['defect']:<20} {row['caught']:>3}/{row['planted']:<3}")
        print(f"Recall {t['recall']:.1%} | clean documents flagged: {t['clean_docs_flagged']}")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        build(conn, args.out)
        print(f"\nScorecard: {args.out}")
    else:
        from .web import create_app
        ensure_data(conn)
        conn.close()
        create_app(args.db).run(port=args.port, debug=False)

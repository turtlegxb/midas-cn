from __future__ import annotations

import argparse

from midas_cn.config import load_config
from midas_cn.orchestration.factory import build_trading_desk


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run A-share trading decision pipeline.")
    parser.add_argument("--config", default="config/system.toml", help="Path to TOML config.")
    parser.add_argument("--symbols", nargs="*", help="A-share symbols, e.g. 600519.SH 300750.SZ")
    parser.add_argument("--no-archive", action="store_true", help="Run without writing archive JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    desk = build_trading_desk(config)
    decision_run, archive_path = desk.run(args.symbols, persist=not args.no_archive)

    print(f"run_id: {decision_run.run_id}")
    for decision in decision_run.decisions:
        print(
            f"{decision.symbol} {decision.signal.value} "
            f"score={decision.score:.3f} confidence={decision.confidence:.3f} "
            f"max_position={decision.risk_plan.max_position:.2%}"
        )
        print(f"  {decision.rationale}")
    if archive_path:
        print(f"archive: {archive_path}")
    report_paths = decision_run.metadata.get("report_paths", {})
    if report_paths:
        if "report_markdown" in report_paths:
            print(f"report_markdown: {report_paths['report_markdown']}")
        if "report_json" in report_paths:
            print(f"report_json: {report_paths['report_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

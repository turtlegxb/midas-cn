from __future__ import annotations

import argparse
from datetime import datetime
import sys

from midas_cn.config import load_config
from midas_cn.orchestration.factory import build_trading_desk
from midas_cn.storage.cache_status import clear_cache, collect_cache_status, resolve_cache_targets


class TerminalProgress:
    def __init__(self, enabled: bool = True, stream=None, width: int = 24):
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.width = width

    def __call__(self, step: int, total: int, message: str) -> None:
        if not self.enabled:
            return
        filled = int(self.width * step / total)
        bar = "#" * filled + "-" * (self.width - filled)
        percent = int(100 * step / total)
        print(f"[{bar}] {step:02d}/{total:02d} {percent:3d}%  {message}", file=self.stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run A-share trading decision pipeline.")
    subparsers = parser.add_subparsers(dest="command")
    report_parser = subparsers.add_parser("report", help="Generate daily report.")
    add_report_args(report_parser)
    cache_parser = subparsers.add_parser("cache", help="Inspect local caches.")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command")
    cache_status_parser = cache_subparsers.add_parser("status", help="Show cache status.")
    cache_status_parser.add_argument("--config", default="config/system.toml", help="Path to TOML config.")
    cache_clear_parser = cache_subparsers.add_parser("clear", help="Clear local caches.")
    cache_clear_parser.add_argument("--config", default="config/system.toml", help="Path to TOML config.")
    cache_clear_parser.add_argument("--target", default="all", help="Cache target name, e.g. K线, 个股新闻, 选股池, or all.")
    sources_parser = subparsers.add_parser("sources", help="Inspect data sources.")
    sources_subparsers = sources_parser.add_subparsers(dest="sources_command")
    sources_check_parser = sources_subparsers.add_parser("check", help="Run a lightweight source check.")
    sources_check_parser.add_argument("--config", default="config/system.toml", help="Path to TOML config.")
    sources_check_parser.add_argument("--symbol", default=None, help="A-share symbol to check, e.g. 600519.SH.")
    add_report_args(parser)
    return parser


def add_report_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config/system.toml", help="Path to TOML config.")
    parser.add_argument("--symbols", nargs="*", help="A-share symbols, e.g. 600519.SH 300750.SZ")
    parser.add_argument("--no-archive", action="store_true", help="Run without writing archive JSON.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logs.")
    parser.add_argument("--trade-date", help="Report trade date in YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument(
        "--refresh",
        default="",
        help=(
            "Clear selected caches before generating the report. "
            "Comma-separated aliases: xueqiu, news, kline, index, pools, all."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "cache":
        if args.cache_command == "status":
            return cache_status(args.config)
        if args.cache_command == "clear":
            return cache_clear(args.config, args.target)
        raise SystemExit("cache requires a subcommand, e.g. cache status")
    if args.command == "sources":
        if args.sources_command == "check":
            return sources_check(args.config, args.symbol)
        raise SystemExit("sources requires a subcommand, e.g. sources check")
    return run_report(args)


def run_report(args) -> int:
    config = load_config(args.config)
    if getattr(args, "refresh", ""):
        root = config.data_cache_dir.parents[1]
        targets = ",".join(sorted(resolve_cache_targets(args.refresh)))
        removed = clear_cache(root, target=args.refresh)
        print(f"refresh_targets: {targets}")
        if removed:
            for path in removed:
                print(f"refresh_removed: {path}")
        else:
            print("refresh_removed: none")
    desk = build_trading_desk(config)
    now = parse_trade_date(args.trade_date) if args.trade_date else None
    decision_run, archive_path = desk.run(
        args.symbols,
        persist=not args.no_archive,
        now=now,
        progress=TerminalProgress(enabled=not args.quiet),
    )

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


def parse_trade_date(value: str) -> datetime:
    normalized = value.replace("-", "")
    if len(normalized) != 8 or not normalized.isdigit():
        raise SystemExit("--trade-date must be YYYYMMDD or YYYY-MM-DD")
    return datetime.strptime(normalized + "155500", "%Y%m%d%H%M%S")


def cache_status(config_path: str) -> int:
    config = load_config(config_path)
    ttl_seconds = int(config.section("cache").get("ttl_seconds", 86_400))
    root = config.data_cache_dir.parents[1]
    rows = collect_cache_status(root, ttl_seconds=ttl_seconds)
    print(f"cache_ttl_seconds: {ttl_seconds}")
    print("| 类型 | 文件数 | 有效 | 过期 | 大小KB | 路径 |")
    print("| --- | ---: | ---: | ---: | ---: | --- |")
    for item in rows:
        print(
            f"| {item.name} | {item.files} | {item.valid} | {item.expired} | "
            f"{item.size_bytes / 1024:.1f} | {item.path} |"
        )
    return 0


def cache_clear(config_path: str, target: str = "all") -> int:
    config = load_config(config_path)
    root = config.data_cache_dir.parents[1]
    removed = clear_cache(root, target=target)
    if removed:
        for path in removed:
            print(f"removed: {path}")
    else:
        print("removed: none")
    return 0


def sources_check(config_path: str, symbol: str | None = None) -> int:
    config = load_config(config_path)
    desk = build_trading_desk(config)
    check_symbol = symbol or (config.default_symbols[0] if config.default_symbols else "600519.SH")
    rows = []
    try:
        market = desk.provider.get_market_snapshot(config.benchmark_symbols)
        rows.append(("市场快照", "success", f"trend={market.benchmark_trend:.2f}, breadth={market.breadth_score:.2f}"))
    except Exception as exc:
        rows.append(("市场快照", "failed", f"{type(exc).__name__}: {exc}"))
    try:
        market_news = desk.provider.get_market_news_results(
            lookback_days=int(config.section("news").get("lookback_days", 2)),
            limit=10,
        )
        ok = sum(1 for item in market_news if item.status.value in {"success", "fallback"})
        rows.append(("市场新闻/政策", "success" if ok else "failed", f"sources={len(market_news)}, usable={ok}"))
    except Exception as exc:
        rows.append(("市场新闻/政策", "failed", f"{type(exc).__name__}: {exc}"))
    try:
        security = desk.provider.get_security_context(check_symbol)
        rows.append(("个股上下文", "success", f"{security.symbol} {security.name} price={security.price:.2f}"))
    except Exception as exc:
        rows.append(("个股上下文", "failed", f"{type(exc).__name__}: {exc}"))
    print("| 检查项 | 状态 | 摘要 |")
    print("| --- | --- | --- |")
    for name, status, summary in rows:
        print(f"| {name} | {status} | {summary.replace('|', '/')} |")
    return 0 if all(status != "failed" for _, status, _ in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

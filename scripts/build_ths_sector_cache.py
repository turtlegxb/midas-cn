from __future__ import annotations

import argparse
import json
from pathlib import Path

import akshare as ak

from midas_cn.config import load_config
from midas_cn.pools.ths_cache import build_ths_sector_cache, save_ths_sector_cache
from midas_cn.pools.storage import StockPoolArchive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Tonghuashun industry and concept cache.")
    parser.add_argument("--config", default="config/system.toml")
    parser.add_argument("--output", help="Output JSON path. Defaults to [ths_cache].path.")
    parser.add_argument("--max-industries", type=int, help="Limit industry boards for smoke tests.")
    parser.add_argument("--max-concepts", type=int, help="Limit concept boards for smoke tests.")
    parser.add_argument("--request-interval-seconds", type=float, help="Sleep between constituent requests.")
    parser.add_argument("--symbols", nargs="*", help="Symbols to enrich, e.g. 002475.SZ 300750.SZ.")
    parser.add_argument("--pool-date", help="Load symbols from output/pools/<YYYYMMDD>.json.")
    parser.add_argument("--no-symbols", action="store_true", help="Only cache board lists, not symbol mappings.")
    parser.add_argument("--skip-board-lists", action="store_true", help="Skip industry/concept board lists and only enrich symbols.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    cache_config = config.section("ths_cache")
    output_path = config.ths_sector_cache_path if not args.output else Path(args.output).expanduser()
    max_industries = args.max_industries if args.max_industries is not None else int(cache_config.get("max_industries", 90))
    max_concepts = args.max_concepts if args.max_concepts is not None else int(cache_config.get("max_concepts", 390))
    request_interval = (
        args.request_interval_seconds
        if args.request_interval_seconds is not None
        else float(cache_config.get("request_interval_seconds", 0.2))
    )
    symbols = [] if args.no_symbols else _resolve_symbols(config, args.symbols or [], args.pool_date)

    def progress(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    payload = build_ths_sector_cache(
        ak,
        max_industries=max_industries,
        max_concepts=max_concepts,
        symbols=symbols,
        include_board_lists=not args.skip_board_lists,
        request_interval_seconds=request_interval,
        progress=progress,
    )
    path = save_ths_sector_cache(output_path, payload)
    if args.quiet:
        print(path)
    else:
        print(
            json.dumps(
                {
                    "path": str(path),
                    "industries": len(payload.get("industries") or []),
                    "concepts": len(payload.get("concepts") or []),
                    "symbols": len(payload.get("symbols") or {}),
                    "errors": len(payload.get("errors") or []),
                },
                ensure_ascii=False,
            )
        )
    return 0


def _resolve_symbols(config, explicit_symbols: list[str], pool_date: str | None) -> list[str]:
    symbols = list(explicit_symbols)
    if pool_date:
        archive = StockPoolArchive(config.pool_archive_dir, ttl_seconds=0)
        for pool in archive.load(pool_date):
            symbols.extend(entry.symbol for entry in pool.entries)
    if not symbols:
        symbols = list(config.default_symbols)
    seen = set()
    unique = []
    for symbol in symbols:
        if symbol not in seen:
            unique.append(symbol)
            seen.add(symbol)
    return unique


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import akshare as ak

from midas_cn.pools.builder import AkShareStockPoolBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="Build A-share stock pools.")
    parser.add_argument("--date", help="Trade date in YYYYMMDD. Defaults to latest weekday.")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    pools = AkShareStockPoolBuilder(ak, top_n=args.top_n).build(args.date)
    payload = json.dumps([asdict(pool) for pool in pools], ensure_ascii=False, indent=2, default=str)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
        print(str(output_path))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

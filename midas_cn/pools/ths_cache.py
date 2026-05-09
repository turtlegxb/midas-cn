from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib import parse, request

from midas_cn.data.news import row_value
from midas_cn.universe.symbols import normalize_symbol


ProgressFn = Callable[[str], None]
THS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def build_ths_sector_cache(
    akshare_module,
    *,
    symbols: list[str] | None = None,
    max_industries: int | None = None,
    max_concepts: int | None = None,
    include_board_lists: bool = True,
    request_interval_seconds: float = 0.2,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    if include_board_lists:
        industries, industry_errors = _safe_board_rows(akshare_module, "industry", max_industries)
        concepts, concept_errors = _safe_board_rows(akshare_module, "concept", max_concepts)
        errors = industry_errors + concept_errors
    else:
        industries, concepts, errors = [], [], []
    symbol_rows: dict[str, dict[str, Any]] = {}
    normalized_symbols = []
    for symbol in symbols or []:
        try:
            normalized = normalize_symbol(symbol)
        except ValueError as exc:
            errors.append(f"symbol:{symbol}:{exc}")
            continue
        if normalized not in normalized_symbols:
            normalized_symbols.append(normalized)

    total = len(normalized_symbols)
    for index, symbol in enumerate(normalized_symbols, start=1):
        if progress:
            progress(f"缓存同花顺F10行业/概念：{symbol} ({index}/{total})")
        try:
            symbol_rows[symbol] = fetch_ths_symbol_profile(akshare_module, symbol)
        except Exception as exc:
            errors.append(f"symbol:{symbol}:{type(exc).__name__}: {exc}")
        if request_interval_seconds > 0:
            time.sleep(request_interval_seconds)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "同花顺F10",
        "industries": industries,
        "concepts": concepts,
        "symbols": symbol_rows,
        "errors": errors,
    }


def fetch_ths_symbol_profile(akshare_module, symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    code = normalized.split(".", 1)[0]
    name = ""
    industry = ""
    concepts: list[str] = []
    market = _ths_market_id(normalized)

    try:
        html = _fetch_text(f"https://basic.10jqka.com.cn/{code}/", encoding="gbk")
        name = _regex_value(html, r'id="stockName"\s+type="hidden"\s+value="([^"]+)"')
        market = _regex_value(html, r'id="marketId"\s+type="hidden"\s+value="([^"]+)"') or market
        industry = _extract_industry_from_html(html)
    except Exception:
        pass

    concepts = _fetch_theme_titles(code, market)
    if not industry:
        industry = _industry_from_akshare(akshare_module, code)
    return {
        "name": name or code,
        "industry": industry,
        "industry_source": "同花顺F10" if industry and not industry.startswith("akshare:") else "AkShare兜底",
        "concepts": concepts,
        "concept_source": "同花顺F10题材接口" if concepts else "",
    }


def load_ths_sector_cache(path: Path, ttl_seconds: int = 86_400) -> dict[str, Any]:
    if not path.exists():
        return {}
    if ttl_seconds > 0:
        expires_at = datetime.fromtimestamp(path.stat().st_mtime) + timedelta(seconds=ttl_seconds)
        if datetime.now() >= expires_at:
            return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_ths_sector_cache(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def symbol_classification(cache: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbols = cache.get("symbols") or {}
    item = dict(symbols.get(normalize_symbol(symbol)) or {})
    if item.get("industry", "").startswith("akshare:"):
        item["industry"] = item["industry"].split(":", 1)[1]
    return item


def _safe_board_rows(akshare_module, board_type: str, limit: int | None) -> tuple[list[dict[str, str]], list[str]]:
    fetch = getattr(akshare_module, f"stock_board_{board_type}_name_ths", None)
    if not fetch:
        return [], [f"akshare missing stock_board_{board_type}_name_ths"]
    try:
        return _board_rows(fetch(), limit), []
    except Exception as exc:
        return [], [f"{board_type}:list:{type(exc).__name__}: {exc}"]


def _board_rows(frame: object, limit: int | None) -> list[dict[str, str]]:
    rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame or [])
    boards = [
        {"name": str(row_value(row, "name", "名称") or ""), "code": str(row_value(row, "code", "代码") or "")}
        for row in rows
    ]
    boards = [item for item in boards if item["name"]]
    return boards[:limit] if limit and limit > 0 else boards


def _fetch_theme_titles(code: str, market: str) -> list[str]:
    url = (
        "https://basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/theme_key_points?"
        + parse.urlencode({"subject": f"{market}-{code}"})
    )
    raw = _fetch_text(url, referer=f"https://basic.10jqka.com.cn/{code}/")
    payload = json.loads(raw)
    if payload.get("status_code") != 0:
        return []
    titles = []
    for item in payload.get("data") or []:
        title = str(item.get("title") or "").strip()
        title = re.sub(r"^要点[一二三四五六七八九十\d]+[:：]", "", title).strip()
        if title and title not in titles:
            titles.append(title)
    return titles[:8]


def _fetch_text(url: str, *, encoding: str = "utf-8", referer: str | None = None) -> str:
    headers = {"User-Agent": THS_USER_AGENT}
    if referer:
        headers["Referer"] = referer
    http_request = request.Request(url, headers=headers)
    with request.urlopen(http_request, timeout=12) as response:
        body = response.read()
    return body.decode(encoding, errors="ignore")


def _regex_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _extract_industry_from_html(html: str) -> str:
    value = _regex_value(html, r'id="companyInfoIndustry"[^>]*title="([^"]+)"')
    if value:
        return value
    match = re.search(r"所属行业：</span>\s*<span[^>]*>(.*?)</span>", html, flags=re.S)
    if not match:
        return ""
    return re.sub(r"<.*?>|\s+", "", match.group(1)).strip()


def _industry_from_akshare(akshare_module, code: str) -> str:
    industry = _industry_from_cninfo(akshare_module, code)
    if industry:
        return f"akshare:{industry}"
    fetch = getattr(akshare_module, "stock_individual_info_em", None)
    if not fetch:
        return ""
    try:
        frame = fetch(symbol=code)
        rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame)
    except Exception:
        return ""
    for row in rows:
        if str(row_value(row, "item") or "").strip() == "行业":
            value = str(row_value(row, "value") or "").strip()
            return f"akshare:{value}" if value else ""
    return ""


def _industry_from_cninfo(akshare_module, code: str) -> str:
    fetch = getattr(akshare_module, "stock_industry_change_cninfo", None)
    if not fetch:
        return ""
    try:
        frame = fetch(symbol=code, start_date="19900101", end_date=datetime.now().strftime("%Y%m%d"))
        rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame)
    except Exception:
        return ""
    rows = sorted(rows, key=lambda row: str(row_value(row, "变更日期") or ""), reverse=True)
    preferred = [row for row in rows if "申银万国" in str(row_value(row, "分类标准") or "")]
    for row in preferred + rows:
        for field in ("行业次类", "行业大类", "行业中类", "行业门类"):
            value = row_value(row, field)
            if value is not None and str(value).strip() and str(value).lower() != "nan":
                return str(value).strip()
    return ""


def _ths_market_id(symbol: str) -> str:
    return "17" if symbol.endswith(".SH") else "33"

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import html
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib import error, parse, request

from midas_cn.models import SourceResult, SourceStatus


SYMBOL_PATTERN = re.compile(r"(?:SH|SZ|BJ)?([03689]\d{5})(?:\.(?:SH|SZ|BJ))?", re.I)


@dataclass(frozen=True)
class XueqiuPost:
    account_name: str
    user_id: str
    post_id: str
    title: str
    text: str
    created_at: str | None
    url: str | None
    symbols: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class XueqiuPositionChange:
    portfolio_name: str
    portfolio_symbol: str
    stock_symbol: str
    stock_name: str
    action: str
    weight_before: float | None
    weight_after: float | None
    changed_at: str | None
    source_url: str | None = None


@dataclass(frozen=True)
class XueqiuSnapshot:
    as_of: str
    status: SourceStatus
    posts: list[XueqiuPost] = field(default_factory=list)
    position_changes: list[XueqiuPositionChange] = field(default_factory=list)
    source_result: SourceResult | None = None


class XueqiuClient:
    def __init__(
        self,
        cookie: str,
        *,
        base_url: str = "https://xueqiu.com",
        timeout: float = 15.0,
        request_interval_seconds: float = 2.0,
    ):
        self.cookie = cookie
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.request_interval_seconds = request_interval_seconds
        self._last_request_at = 0.0

    def user_timeline(self, user_id: str, *, page: int = 1, count: int = 20) -> dict[str, Any]:
        return self._get_json(
            "/v4/statuses/user_timeline.json",
            {"user_id": user_id, "page": page, "count": count},
        )

    def user_timeline_url(self, url: str) -> dict[str, Any]:
        return self._get_json_url(url)

    def portfolio_rebalancing_history(self, cube_symbol: str, *, page: int = 1, count: int = 20) -> dict[str, Any]:
        return self._get_json(
            "/cubes/rebalancing/history.json",
            {"cube_symbol": cube_symbol, "page": page, "count": count},
        )

    def _get_json(self, path: str, params: dict[str, object]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{parse.urlencode(params)}"
        return self._get_json_url(url)

    def _get_json_url(self, url: str) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval_seconds:
            time.sleep(self.request_interval_seconds - elapsed)
        http_request = request.Request(
            url,
            headers={
                "Cookie": self.cookie,
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Referer": self.base_url,
            },
            method="GET",
        )
        self._last_request_at = time.monotonic()
        with request.urlopen(http_request, timeout=self.timeout) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read().decode("utf-8", errors="replace")
        if "json" not in content_type.lower():
            raise RuntimeError(_non_json_error(content_type, body))
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(_non_json_error(content_type, body)) from exc


class XueqiuTracker:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def fetch(self, as_of: str) -> XueqiuSnapshot:
        if not bool(self.config.get("enabled", False)):
            return self._missing(as_of, "未开启雪球跟踪")
        if not (self.config.get("influencers") or self.config.get("portfolios")):
            return self._missing(as_of, "未配置雪球大V或公开组合")
        cookie_env = str(self.config.get("cookie_env") or "XUEQIU_COOKIE")
        cookie = os.getenv(cookie_env, "").strip()
        if not cookie:
            return self._missing(as_of, f"未配置环境变量 {cookie_env}")

        client = XueqiuClient(
            cookie,
            timeout=float(self.config.get("timeout_seconds", 15)),
            request_interval_seconds=float(self.config.get("request_interval_seconds", 2)),
        )
        lookback_days = int(self.config.get("lookback_days", 7))
        cutoff = datetime.now() - timedelta(days=lookback_days)
        posts: list[XueqiuPost] = []
        changes: list[XueqiuPositionChange] = []
        errors: list[str] = []

        for account in self.config.get("influencers", []) or []:
            try:
                posts.extend(self._fetch_posts(client, account, cutoff))
            except Exception as exc:
                errors.append(f"{account.get('name') or account.get('user_id')}: {type(exc).__name__}: {exc}")

        for portfolio in self.config.get("portfolios", []) or []:
            try:
                changes.extend(self._fetch_position_changes(client, portfolio, cutoff))
            except Exception as exc:
                errors.append(f"{portfolio.get('name') or portfolio.get('symbol')}: {type(exc).__name__}: {exc}")

        status = SourceStatus.SUCCESS if not errors else SourceStatus.PARTIAL if (posts or changes) else SourceStatus.FAILED
        result = SourceResult(
            data="雪球大V与持仓",
            source="xueqiu",
            provider="雪球公开页面/接口",
            status=status,
            error_message="；".join(errors) if errors else None,
            checked_at=datetime.now().isoformat(),
            context={"posts": str(len(posts)), "position_changes": str(len(changes))},
        )
        return XueqiuSnapshot(as_of=as_of, status=status, posts=posts, position_changes=changes, source_result=result)

    def _fetch_posts(self, client: XueqiuClient, account: dict[str, Any], cutoff: datetime) -> list[XueqiuPost]:
        user_id = str(account.get("user_id") or "").strip()
        timeline_url = str(account.get("timeline_url") or "").strip()
        timeline_url_env = str(account.get("timeline_url_env") or "").strip()
        if timeline_url_env and not timeline_url:
            timeline_url = os.getenv(timeline_url_env, "").strip()
        if not user_id and not timeline_url:
            return []
        max_posts = int(self.config.get("max_posts_per_account", 50))
        payload = client.user_timeline_url(timeline_url) if timeline_url else client.user_timeline(user_id, count=min(max_posts, 50))
        rows = payload.get("statuses") or payload.get("list") or []
        posts = []
        for row in rows[:max_posts]:
            created_at = _parse_xueqiu_time(row.get("created_at"))
            if created_at and created_at < cutoff:
                continue
            text = _clean_html(row.get("text") or row.get("description") or "")
            title = _clean_html(row.get("title") or "")
            full_text = f"{title} {text}".strip()
            post_id = str(row.get("id") or row.get("target") or "")
            posts.append(
                XueqiuPost(
                    account_name=str(account.get("name") or user_id),
                    user_id=user_id or str(row.get("user_id") or ""),
                    post_id=post_id,
                    title=title,
                    text=text,
                    created_at=created_at.isoformat() if created_at else None,
                    url=f"https://xueqiu.com/{user_id}/{post_id}" if post_id else None,
                    symbols=extract_symbols(full_text),
                    metrics={
                        "reply_count": row.get("reply_count"),
                        "retweet_count": row.get("retweet_count"),
                        "like_count": row.get("like_count"),
                    },
                )
            )
        return posts

    def _fetch_position_changes(
        self,
        client: XueqiuClient,
        portfolio: dict[str, Any],
        cutoff: datetime,
    ) -> list[XueqiuPositionChange]:
        cube_symbol = str(portfolio.get("symbol") or "").strip()
        if not cube_symbol:
            return []
        payload = client.portfolio_rebalancing_history(cube_symbol, count=int(self.config.get("max_changes_per_portfolio", 50)))
        rows = payload.get("list") or payload.get("history") or []
        changes: list[XueqiuPositionChange] = []
        for row in rows:
            changed_at = _parse_xueqiu_time(row.get("updated_at") or row.get("created_at") or row.get("time"))
            if changed_at and changed_at < cutoff:
                continue
            rebalancing = row.get("rebalancing_histories") or row.get("rebalancing") or row.get("holdings") or []
            for item in rebalancing:
                stock_symbol = normalize_symbol(str(item.get("stock_symbol") or item.get("symbol") or ""))
                if not stock_symbol:
                    continue
                before = _to_float(item.get("prev_weight") or item.get("weight_old") or item.get("old_weight"))
                after = _to_float(item.get("target_weight") or item.get("weight") or item.get("new_weight"))
                changes.append(
                    XueqiuPositionChange(
                        portfolio_name=str(portfolio.get("name") or cube_symbol),
                        portfolio_symbol=cube_symbol,
                        stock_symbol=stock_symbol,
                        stock_name=str(item.get("stock_name") or item.get("name") or ""),
                        action=_position_action(before, after),
                        weight_before=before,
                        weight_after=after,
                        changed_at=changed_at.isoformat() if changed_at else None,
                        source_url=f"https://xueqiu.com/P/{cube_symbol}",
                    )
                )
        return changes

    def _missing(self, as_of: str, message: str) -> XueqiuSnapshot:
        result = SourceResult(
            data="雪球大V与持仓",
            source="xueqiu",
            provider="雪球公开页面/接口",
            status=SourceStatus.MISSING,
            error_message=message,
            checked_at=datetime.now().isoformat(),
        )
        return XueqiuSnapshot(as_of=as_of, status=SourceStatus.MISSING, source_result=result)


class XueqiuArchive:
    def __init__(self, archive_dir: Path):
        self.archive_dir = archive_dir

    def path_for(self, trade_date: str) -> Path:
        return self.archive_dir / f"{trade_date}.json"

    def load(self, trade_date: str) -> XueqiuSnapshot | None:
        path = self.path_for(trade_date)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return xueqiu_snapshot_from_dict(payload)

    def save(self, trade_date: str, snapshot: XueqiuSnapshot) -> Path:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(trade_date)
        path.write_text(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return path


def xueqiu_snapshot_from_dict(payload: dict[str, Any]) -> XueqiuSnapshot:
    source_result = payload.get("source_result")
    return XueqiuSnapshot(
        as_of=str(payload.get("as_of") or ""),
        status=SourceStatus(str(payload.get("status") or SourceStatus.MISSING.value)),
        posts=[XueqiuPost(**item) for item in payload.get("posts", [])],
        position_changes=[XueqiuPositionChange(**item) for item in payload.get("position_changes", [])],
        source_result=SourceResult(
            data=str(source_result.get("data", "雪球大V与持仓")),
            source=str(source_result.get("source", "xueqiu")),
            provider=str(source_result.get("provider", "雪球公开页面/接口")),
            status=SourceStatus(str(source_result.get("status", SourceStatus.MISSING.value))),
            error_type=source_result.get("error_type"),
            error_message=source_result.get("error_message"),
            checked_at=source_result.get("checked_at"),
            context=dict(source_result.get("context") or {}),
        )
        if isinstance(source_result, dict)
        else None,
    )


def extract_symbols(text: str) -> list[str]:
    symbols = []
    for match in SYMBOL_PATTERN.finditer(text):
        normalized = normalize_symbol(match.group(0))
        if normalized:
            symbols.append(normalized)
    return list(dict.fromkeys(symbols))


def normalize_symbol(value: str) -> str:
    raw = value.upper().replace("$", "").strip()
    match = SYMBOL_PATTERN.search(raw)
    if not match:
        return ""
    code = match.group(1)
    if raw.startswith("SH") or raw.endswith(".SH") or code.startswith(("6", "9")):
        suffix = "SH"
    elif raw.startswith("BJ") or raw.endswith(".BJ") or code.startswith(("8", "4")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(value))
    return " ".join(html.unescape(text).split())


def _parse_xueqiu_time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number)
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", ""))
    except ValueError:
        return None


def _position_action(before: float | None, after: float | None) -> str:
    if before in (None, 0) and after and after > 0:
        return "新进"
    if after in (None, 0) and before and before > 0:
        return "清仓"
    if before is None or after is None:
        return "调整"
    if after > before:
        return "加仓"
    if after < before:
        return "减仓"
    return "持平"


def _non_json_error(content_type: str, body: str) -> str:
    preview = " ".join(body[:240].split())
    if "aliyun_waf" in body or "_waf_" in body:
        return "雪球请求被风控页拦截，返回阿里云WAF挑战页"
    if "登录" in body or "login" in body.lower():
        return "雪球登录态无效或已过期，返回登录页"
    return f"雪球接口未返回JSON，content-type={content_type}，响应摘要={preview}"

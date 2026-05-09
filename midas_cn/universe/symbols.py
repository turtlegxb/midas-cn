from __future__ import annotations


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not value:
        raise ValueError("symbol cannot be empty")
    if "." in value:
        code, exchange = value.split(".", 1)
        return f"{code}.{exchange}"
    if value.startswith(("4", "8")) or value.startswith("920"):
        return f"{value}.BJ"
    if value.startswith(("5", "6", "9")):
        return f"{value}.SH"
    if value.startswith(("0", "1", "2", "3")):
        return f"{value}.SZ"
    raise ValueError(f"cannot infer A-share exchange for symbol: {symbol}")


def normalize_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in symbols:
        item = normalize_symbol(symbol)
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized

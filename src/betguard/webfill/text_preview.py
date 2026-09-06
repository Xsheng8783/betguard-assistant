"""Presentation of existing parser results; no parsing or inferred values."""
from __future__ import annotations


def candidate_preview(result: dict, *, game: str) -> dict:
    kind = result.get("type", "normal")
    rules = result.get("bets") or {}
    amounts = {
        str(s): entry["money"] for s, entry in rules.items()
        if entry.get("money") is not None
    }
    if not rules and result.get("money") is not None:
        amounts = {str(s): result["money"] for s in result.get("stars", [])}
    groups = result.get("columns") if kind == "column" else [result.get("numbers") or []]
    stem = " × ".join(" ".join(f"{int(n):02d}" for n in group) for group in groups or [])
    labels = []
    for star in result.get("stars") or []:
        rule = rules.get(str(star), rules.get(star, {}))
        unit = rule.get("unit", result.get("unit"))
        multiplier = f" ×{unit}" if unit is not None else ""
        labels.append(f"{star}星{multiplier}（{amounts.get(str(star), '?')}元）")
    supported = kind in {"normal", "column"} and bool(amounts) and result.get("game", game) == game
    if kind == "car":
        stem = result.get("original_text") or result.get("raw_text") or result.get("raw") or f"{int(result['number']):02d}"
        labels = [f"車：{result.get('car_units', '?')}，{result.get('money', '?')}元"]
    return {
        "numbers": result.get("numbers") or ([result["number"]] if kind == "car" and result.get("number") else []),
        "columns": result.get("columns"),
        "stars": result.get("stars") or [],
        "money": result.get("money"), "unit": result.get("unit"),
        "amounts": amounts, "bets": rules,
        "type": kind, "bet_type": kind, "game": game,
        "summary": f"{stem}｜{'；'.join(labels)}｜{game}",
        "fill_supported": supported,
        "fill_unsupported_reason": "" if supported else (
            "牌文與工作彩種不一致，請重新選擇彩種並解析。" if result.get("game", game) != game
            else "此玩法可保留解析結果，但目前填入欄位尚未支援；請人工處理。"),
    }

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd


THEME_COLUMN = "相关概念/板块"
THEME_NOTE_COLUMN = "概念匹配说明"
THEME_HIT_COLUMN = "概念库命中数"
DEFAULT_LIBRARY_DIR = Path(__file__).resolve().parent / "kpl_concept_library"
DEFAULT_STOCK_CONCEPT_PATH = DEFAULT_LIBRARY_DIR / "stock_to_concepts.csv"


def normalize_stock_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _unique_keep_order(items: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _split_concepts(value: object) -> list[str]:
    if value is None or pd.isna(value):
        return []
    parts = re.split(r"[、,，/|]+", str(value))
    return _unique_keep_order(part.strip() for part in parts)


def _read_csv_with_fallback(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def load_stock_concept_map(path: str | os.PathLike[str] | None = None) -> dict[str, list[str]]:
    """Load KPL stock -> concepts data.

    Supports the compact stock_to_concepts.csv and the long concept_stock_map.csv
    produced by the local OCR整理工具.
    """
    source = Path(path or os.environ.get("CMLM_STOCK_CONCEPT_PATH", DEFAULT_STOCK_CONCEPT_PATH))
    if not source.exists() or source.stat().st_size == 0:
        return {}

    df = _read_csv_with_fallback(source)
    if {"stock_name", "concepts"}.issubset(df.columns):
        pairs = (
            (normalize_stock_name(row["stock_name"]), _split_concepts(row["concepts"]))
            for _, row in df.iterrows()
        )
    elif {"stock_name", "concept_name"}.issubset(df.columns):
        grouped = df.groupby("stock_name", sort=False)["concept_name"].apply(list)
        pairs = ((normalize_stock_name(name), _unique_keep_order(concepts)) for name, concepts in grouped.items())
    else:
        return {}

    concept_map: dict[str, list[str]] = {}
    for name, concepts in pairs:
        if name and concepts:
            concept_map[name] = concepts
    return concept_map


def _numeric(row: pd.Series, candidates: Iterable[str], default: float = 0.0) -> float:
    for col in candidates:
        if col not in row:
            continue
        value = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
        if pd.notna(value):
            return float(value)
    return default


def _row_strength(row: pd.Series) -> float:
    change = max(_numeric(row, ("涨跌幅(%)", "今日涨幅(%)", "涨跌幅")), 0.0)
    amount_yi = max(_numeric(row, ("今日成交额(亿)",)), 0.0)
    ratio = max(_numeric(row, ("增量倍数",), 1.0), 0.0)
    ratio_bonus = max(ratio - 1.0, 0.0)
    return 1.0 + change * 0.6 + ratio_bonus * 1.5 + math.log1p(amount_yi) * 0.8


def _theme_stats(df: pd.DataFrame, concept_map: dict[str, list[str]]) -> dict[str, dict[str, object]]:
    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {"score": 0.0, "names": set(), "leader": "", "leader_strength": -1.0}
    )
    for _, row in df.iterrows():
        name = normalize_stock_name(row.get("名称"))
        concepts = concept_map.get(name, [])
        strength = _row_strength(row)
        board = str(row.get("板块", "") or "").strip()
        candidates = _unique_keep_order([*concepts, board])
        for concept in candidates:
            item = stats[concept]
            item["score"] = float(item["score"]) + strength
            item["names"].add(name)
            if strength > float(item["leader_strength"]):
                item["leader_strength"] = strength
                item["leader"] = name
    return stats


def _theme_names(stats: dict[str, dict[str, object]], concept: str) -> set[str]:
    names = stats.get(concept, {}).get("names", set())
    return set(names) if isinstance(names, set) else set()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _pick_themes_for_row(
    row: pd.Series,
    concept_map: dict[str, list[str]],
    stats: dict[str, dict[str, object]],
    max_themes: int,
) -> tuple[list[str], str, int]:
    name = normalize_stock_name(row.get("名称"))
    concepts = concept_map.get(name, [])
    if not concepts:
        board = str(row.get("板块", "") or "").strip()
        return ([board] if board else []), "概念库未命中，回退到行业板块。", 0

    board = str(row.get("板块", "") or "").strip()
    candidates = _unique_keep_order([*concepts, board])
    scored: list[tuple[str, float, int, str, int]] = []
    for order, concept in enumerate(candidates):
        item = stats.get(concept)
        if not item:
            continue
        count = len(_theme_names(stats, concept))
        order_weight = max(0.82, 1.18 - order * 0.08)
        score = float(item["score"]) * order_weight + count * 0.8
        leader = str(item.get("leader") or "")
        scored.append((concept, score, count, leader, order))

    if not scored:
        board = str(row.get("板块", "") or "").strip()
        return ([board] if board else []), "概念库命中但当日同池热度不足，回退到行业板块。", len(concepts)

    top_raw_score = max(item[1] for item in scored)
    scored.sort(key=lambda item: (item[1] < top_raw_score * 0.90, item[4], -item[1]))
    kept: list[tuple[str, float, int, str, int]] = []
    for item in scored:
        concept = item[0]
        names = _theme_names(stats, concept)
        if any(_jaccard(names, _theme_names(stats, selected[0])) >= 0.85 for selected in kept):
            continue
        kept.append(item)
        if len(kept) >= max_themes:
            break

    if not kept:
        kept = scored[:1]

    top_score = kept[0][1]
    selected = [kept[0]]
    for item in kept[1:max_themes]:
        if item[1] >= top_score * 0.58:
            selected.append(item)

    if len(selected) > 1 and selected[0][1] >= selected[1][1] * 1.45 and selected[0][2] >= 2:
        selected = selected[:1]

    note_bits = [
        f"{concept}同池{count}只/热度{score:.1f}" + (f"/领涨{leader}" if leader else "")
        for concept, score, count, leader, _order in selected
    ]
    return [item[0] for item in selected], "；".join(note_bits), len(concepts)


def attach_best_concepts(
    df: pd.DataFrame,
    concept_map: dict[str, list[str]] | None = None,
    max_themes: int = 3,
) -> pd.DataFrame:
    """Attach the 1-3 most relevant same-day KPL concepts/boards to signal rows."""
    result = df.copy()
    if result.empty:
        for col in (THEME_COLUMN, THEME_NOTE_COLUMN, THEME_HIT_COLUMN):
            if col not in result.columns:
                result[col] = pd.Series(dtype="object")
        return result

    concept_map = concept_map or {}
    stats = _theme_stats(result, concept_map)
    themes: list[str] = []
    notes: list[str] = []
    hits: list[int] = []
    for _, row in result.iterrows():
        picked, note, hit_count = _pick_themes_for_row(row, concept_map, stats, max(1, max_themes))
        themes.append("、".join(picked))
        notes.append(note)
        hits.append(hit_count)

    insert_after = "板块" if "板块" in result.columns else result.columns[min(1, len(result.columns) - 1)]
    result[THEME_COLUMN] = themes
    result[THEME_NOTE_COLUMN] = notes
    result[THEME_HIT_COLUMN] = hits

    ordered = list(result.columns)
    for col in (THEME_HIT_COLUMN, THEME_NOTE_COLUMN, THEME_COLUMN):
        if col in ordered:
            ordered.remove(col)
    insert_at = ordered.index(insert_after) + 1 if insert_after in ordered else len(ordered)
    for col in (THEME_COLUMN, THEME_NOTE_COLUMN, THEME_HIT_COLUMN):
        ordered.insert(insert_at, col)
        insert_at += 1
    return result[ordered]

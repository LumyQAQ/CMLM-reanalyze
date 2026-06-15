from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from mootdx.quotes import Quotes


BASE_DIR = Path(__file__).resolve().parent

MAP_FILE_PATH = BASE_DIR / "stock_to_sector.csv"
SURGE_TREND_PATH = BASE_DIR / "v4_surge_trend.csv"
SURGE_RANGE_PATH = BASE_DIR / "v4_surge_range.csv"
PULLBACK_PATH = BASE_DIR / "v4_pullback_candidates.csv"

TREND_COLUMNS = [
    "代码",
    "名称",
    "板块",
    "涨跌幅(%)",
    "3日涨幅(%)",
    "5日涨幅(%)",
    "增量倍数",
    "今日成交额(亿)",
    "常态均额(亿)",
    "逻辑标签",
]
RANGE_COLUMNS = [
    "代码",
    "名称",
    "板块",
    "涨跌幅(%)",
    "3日涨幅(%)",
    "5日涨幅(%)",
    "突破类型",
    "增量倍数",
    "今日成交额(亿)",
]
PULLBACK_COLUMNS = [
    "代码",
    "名称",
    "板块",
    "今日涨幅(%)",
    "3日涨幅(%)",
    "5日涨幅(%)",
    "回踩天数",
    "今日量/爆发量",
    "爆发日强度",
    "今日成交额(亿)",
]


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}")


def clean_stock_code(code_series: pd.Series) -> pd.Series:
    return code_series.astype(str).str.extract(r"(\d{6})")[0]


def safe_round(value: Any, digits: int = 2) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def get_volume_factor(now: datetime | None = None) -> float:
    """Return the intraday volume extrapolation factor for A-share sessions."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return 1.0

    current_mins = now.hour * 60 + now.minute
    if current_mins < 570:
        return 1.0
    if 570 <= current_mins <= 690:
        elapsed = current_mins - 570
        return 240.0 / elapsed if elapsed > 0 else 1.0
    if 690 < current_mins < 780:
        return 2.0
    if 780 <= current_mins <= 900:
        elapsed = 120 + (current_mins - 780)
        return 240.0 / elapsed if elapsed > 0 else 1.0
    return 1.0


def normalize_kline_frame(df_k: pd.DataFrame) -> pd.DataFrame:
    """Normalize mootdx daily K-line data into chronological row order."""
    df = df_k.copy()
    if df.index.name == "datetime" and "datetime" not in df.columns:
        df = df.reset_index()
    else:
        df.index.name = None
        df = df.reset_index(drop=True)

    if "datetime" in df.columns:
        df = df.sort_values("datetime").reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)
    df.index.name = None

    if "close" in df.columns:
        df["pct_change"] = df["close"].pct_change() * 100
    return df


def calculate_momentum_fields(df_k: pd.DataFrame) -> dict[str, float | None]:
    latest_close = pd.to_numeric(df_k["close"], errors="coerce").iloc[-1]
    fields: dict[str, float | None] = {}
    for days, column in [(3, "3日涨幅(%)"), (5, "5日涨幅(%)")]:
        if len(df_k) <= days:
            fields[column] = None
            continue
        base_close = pd.to_numeric(df_k["close"], errors="coerce").iloc[-days - 1]
        if pd.isna(base_close) or base_close <= 0 or pd.isna(latest_close):
            fields[column] = None
        else:
            fields[column] = round((latest_close - base_close) / base_close * 100, 2)
    return fields


def write_result_csv(path: Path, rows: list[dict[str, Any]], columns: list[str], sort_by: str | None = None) -> None:
    df = pd.DataFrame(rows, columns=columns)
    if sort_by and sort_by in df.columns and not df.empty:
        df = df.sort_values(sort_by, ascending=False)
    df.to_csv(path, index=False)


def load_mapping_table(path: Path = MAP_FILE_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing stock mapping file: {path}")

    last_error: Exception | None = None
    df_map: pd.DataFrame | None = None
    for encoding in ["utf-16", "gbk", "utf-8-sig", "utf-8"]:
        try:
            candidate = pd.read_csv(path, encoding=encoding, sep=None, engine="python")
            if len(candidate.columns) > 1:
                df_map = candidate
                break
        except Exception as exc:
            last_error = exc

    if df_map is None:
        raise RuntimeError(f"unable to parse mapping file {path}: {last_error}")

    def find_column(keyword: str) -> str:
        matches = [column for column in df_map.columns if keyword in str(column)]
        if not matches:
            raise ValueError(f"mapping file missing column containing: {keyword}")
        return matches[0]

    code_col = find_column("代码")
    name_col = find_column("名称")
    industry_col = find_column("行业")

    normalized = df_map[[code_col, name_col, industry_col]].copy()
    normalized.columns = ["代码", "名称", "板块"]
    normalized["代码"] = clean_stock_code(normalized["代码"])
    normalized = normalized[normalized["代码"].str.match(r"^(60|00|30|68)\d{4}$", na=False)]
    normalized.dropna(subset=["代码", "名称", "板块"], inplace=True)
    return normalized.drop_duplicates(subset=["代码"])


def prefixed_symbol(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_bars(client: Any, code: str, offset: int) -> pd.DataFrame:
    df_k = client.bars(symbol=code, frequency=9, offset=offset)
    if df_k is None or df_k.empty:
        df_k = client.bars(symbol=prefixed_symbol(code), frequency=9, offset=offset)
    if df_k is None or df_k.empty:
        return pd.DataFrame()
    return normalize_kline_frame(df_k)


def append_warning(warnings: list[str], message: str, limit: int = 30) -> None:
    if len(warnings) < limit:
        warnings.append(message)


def close_client(client: Any) -> None:
    nested = getattr(client, "client", None)
    closer = getattr(nested, "close", None)
    if callable(closer):
        closer()


def run_v4_engine() -> None:
    log("启动 CMLM V4.6 引擎：趋势/区间/回踩三频段扫描")
    warnings: list[str] = []

    vol_factor = get_volume_factor()
    log(f"量能外推系数: {vol_factor:.2f}x")

    log("[1/5] 加载本地映射表")
    df_map = load_mapping_table()
    log(f"映射表有效标的: {len(df_map)}")

    log("[2/5] 拉取全市场快照")
    client = Quotes.factory(market="std")
    try:
        symbol_list = df_map["代码"].tolist()
        all_quotes: list[pd.DataFrame] = []
        failed_quote_batches = 0
        for i in range(0, len(symbol_list), 80):
            chunk = symbol_list[i : i + 80]
            try:
                result = client.quotes(symbol=chunk)
                if isinstance(result, pd.DataFrame) and not result.empty:
                    all_quotes.append(result)
                elif isinstance(result, list) and result:
                    all_quotes.append(pd.DataFrame(result))
                time.sleep(0.01)
            except Exception as exc:
                failed_quote_batches += 1
                append_warning(warnings, f"quotes batch {i // 80 + 1} failed: {exc}")

        if not all_quotes:
            raise RuntimeError("no quote data returned from mootdx")

        df_quotes = pd.concat(all_quotes, ignore_index=True)
        required_quote_cols = {"code", "price", "last_close", "amount"}
        missing_quote_cols = required_quote_cols - set(df_quotes.columns)
        if missing_quote_cols:
            raise RuntimeError(f"quote data missing columns: {sorted(missing_quote_cols)}")

        df_quotes["代码"] = clean_stock_code(df_quotes["code"])
        log(f"快照有效批次: {len(all_quotes)}, 失败批次: {failed_quote_batches}")

        log("[3/5] 合成基本数据")
        df_merged = pd.merge(
            df_map,
            df_quotes[["代码", "price", "last_close", "vol", "amount"]],
            on="代码",
            how="inner",
        )
        df_merged = df_merged[df_merged["last_close"] > 0].copy()
        df_merged["涨跌幅"] = (df_merged["price"] - df_merged["last_close"]) / df_merged["last_close"] * 100
        log(f"合成有效标的: {len(df_merged)}")

        log("[4/5] 扫描右侧趋势与区间突破")
        df_surge = df_merged[df_merged["涨跌幅"] >= 2.5].copy()
        trend_results: list[dict[str, Any]] = []
        range_results: list[dict[str, Any]] = []
        failed_bars = 0

        for _, row in df_surge.iterrows():
            code = row["代码"]
            today_amt = float(row["amount"])
            today_change = float(row["涨跌幅"])
            try:
                df_k = fetch_bars(client, code, offset=60)
                if df_k.empty or len(df_k) < 45:
                    continue

                momentum = calculate_momentum_fields(df_k)
                past_15_amts = pd.to_numeric(df_k["amount"].iloc[-16:-1], errors="coerce").dropna()
                if len(past_15_amts) < 2:
                    continue
                true_base_amt = past_15_amts.drop(past_15_amts.idxmax()).mean()
                has_recent_breakout = (df_k["pct_change"].iloc[-16:-1] >= 8.0).any()

                if true_base_amt <= 0:
                    continue

                est_today_amt = today_amt * vol_factor
                ratio = est_today_amt / true_base_amt
                recent_40_closes = pd.to_numeric(df_k["close"].iloc[-41:-1], errors="coerce").dropna()
                if recent_40_closes.empty:
                    continue
                prev_high_close = recent_40_closes.max()
                prev_high_idx = recent_40_closes.idxmax()
                days_since_high = len(df_k) - 1 - prev_high_idx
                today_close = float(df_k["close"].iloc[-1])

                cond_a = today_change >= 5.0 and ratio >= 1.35
                cond_b = 3.0 <= today_change < 5.0 and ratio >= 1.25
                cond_c = has_recent_breakout and today_change >= 2.5 and ratio >= 1.1

                if cond_a or cond_b or cond_c:
                    if cond_a:
                        reason = "强势放量突破"
                    elif cond_c:
                        reason = "回调二波起涨"
                    else:
                        reason = "趋势放量异动"
                    trend_results.append(
                        {
                            "代码": code,
                            "名称": row["名称"],
                            "板块": row["板块"],
                            "涨跌幅(%)": round(today_change, 2),
                            **momentum,
                            "增量倍数": round(ratio, 2),
                            "今日成交额(亿)": round(today_amt / 1e8, 2),
                            "常态均额(亿)": round(true_base_amt / 1e8, 2),
                            "逻辑标签": reason,
                        }
                    )

                if today_change >= 4.0 and days_since_high >= 4 and today_close > prev_high_close and ratio >= 1.5:
                    range_results.append(
                        {
                            "代码": code,
                            "名称": row["名称"],
                            "板块": row["板块"],
                            "涨跌幅(%)": round(today_change, 2),
                            **momentum,
                            "突破类型": f"突破 {days_since_high} 天前高",
                            "增量倍数": round(ratio, 2),
                            "今日成交额(亿)": round(today_amt / 1e8, 2),
                        }
                    )
            except Exception as exc:
                failed_bars += 1
                append_warning(warnings, f"trend/range bars failed for {code}: {exc}")

        write_result_csv(SURGE_TREND_PATH, trend_results, TREND_COLUMNS, sort_by="增量倍数")
        write_result_csv(SURGE_RANGE_PATH, range_results, RANGE_COLUMNS, sort_by="增量倍数")
        log(f"趋势结果: {len(trend_results)}, 区间突破: {len(range_results)}, K线失败: {failed_bars}")

        log("[5/5] 扫描缩量回踩")
        df_pullback = df_merged[
            (df_merged["涨跌幅"] <= 1.5) & (df_merged["涨跌幅"] >= -7.0) & (df_merged["amount"] >= 30_000_000)
        ].copy()
        pullback_results: list[dict[str, Any]] = []
        failed_pullback_bars = 0

        for _, row in df_pullback.iterrows():
            code = row["代码"]
            today_amt = float(row["amount"])
            today_change = float(row["涨跌幅"])
            try:
                df_k = fetch_bars(client, code, offset=30)
                if df_k.empty or len(df_k) < 20:
                    continue

                momentum = calculate_momentum_fields(df_k)
                recent_window = df_k.iloc[-7:-1]
                breakout_idx = None
                breakout_ratio = 0.0
                for i in range(len(recent_window)):
                    real_idx = recent_window.index[i]
                    if df_k["pct_change"].iloc[real_idx] < 5.0:
                        continue
                    past_15 = pd.to_numeric(df_k["amount"].iloc[real_idx - 15 : real_idx], errors="coerce").dropna()
                    if len(past_15) != 15:
                        continue
                    base_amt = past_15.drop(past_15.idxmax()).mean()
                    if base_amt > 0 and df_k["amount"].iloc[real_idx] / base_amt >= 1.5:
                        breakout_idx = real_idx
                        breakout_ratio = float(df_k["amount"].iloc[real_idx] / base_amt)

                if breakout_idx is None:
                    continue

                today_close = float(df_k["close"].iloc[-1])
                yest_close = float(df_k["close"].iloc[-2])
                breakout_amt = float(df_k["amount"].iloc[breakout_idx])
                breakout_open = float(df_k["open"].iloc[breakout_idx])
                yest_amt = float(df_k["amount"].iloc[-2])
                est_today_amt = today_amt * vol_factor

                vol_shrink = est_today_amt < yest_amt and est_today_amt < breakout_amt * 0.65
                price_drop = today_close < yest_close
                holding_support = today_close > breakout_open

                if vol_shrink and price_drop and holding_support:
                    days_since = len(df_k) - 1 - breakout_idx
                    raw_shrink = round(today_amt / breakout_amt * 100, 1)
                    estimated_shrink = round(est_today_amt / breakout_amt * 100, 1)
                    shrink_text = (
                        f"{raw_shrink}% (预估 {estimated_shrink}%)" if vol_factor > 1.0 else f"{raw_shrink}%"
                    )
                    pullback_results.append(
                        {
                            "代码": code,
                            "名称": row["名称"],
                            "板块": row["板块"],
                            "今日涨幅(%)": round(today_change, 2),
                            **momentum,
                            "回踩天数": f"{days_since} 天",
                            "今日量/爆发量": shrink_text,
                            "爆发日强度": f"放量 {round(breakout_ratio, 1)}倍",
                            "今日成交额(亿)": round(today_amt / 1e8, 2),
                            "_shrink_sort": estimated_shrink,
                        }
                    )
            except Exception as exc:
                failed_pullback_bars += 1
                append_warning(warnings, f"pullback bars failed for {code}: {exc}")

        if pullback_results:
            pullback_results = sorted(pullback_results, key=lambda item: item["_shrink_sort"])
            for item in pullback_results:
                item.pop("_shrink_sort", None)
        write_result_csv(PULLBACK_PATH, pullback_results, PULLBACK_COLUMNS)
        log(f"回踩结果: {len(pullback_results)}, K线失败: {failed_pullback_bars}")

        if warnings:
            log(f"采集警告 {len(warnings)} 条，前 {min(len(warnings), 30)} 条如下")
            for warning in warnings:
                log(f"WARN {warning}")
        log("V4.6 引擎处理完毕，三大独立池文件已生成")
    finally:
        close_client(client)


if __name__ == "__main__":
    try:
        run_v4_engine()
    except Exception as exc:
        log(f"ERROR {exc}")
        raise SystemExit(1) from exc

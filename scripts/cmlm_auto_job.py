#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
CMLM_DIR = Path(os.environ.get("CMLM_DIR", REPO_ROOT)).expanduser().resolve()
ENGINE_PATH = Path(os.environ.get("CMLM_ENGINE", CMLM_DIR / "v4_engine.py")).expanduser()
REMOTE_URL = os.environ.get("CMLM_REMOTE_URL", "https://github.com/LumyQAQ/CMLM-reanalyze.git")
PUBLISH_DIR = Path(os.environ.get("CMLM_PUBLISH_DIR", "/tmp/cmlm_reanalyze_publish_live")).expanduser()
DRYRUN_PUBLISH_DIR = Path(os.environ.get("CMLM_DRYRUN_PUBLISH_DIR", f"{PUBLISH_DIR}_dryrun")).expanduser()
OUTPUT_MD = CMLM_DIR / "v4_cmlm_analysis_latest.md"
OUTPUT_JSON = CMLM_DIR / "v4_cmlm_analysis_latest.json"
LOG_DIR = CMLM_DIR / "logs"
ENV_FILES = [CMLM_DIR / "cmlm_auto.env", REPO_ROOT / "scripts" / "cmlm_auto.env"]
GITHUB_ANALYSIS_URL = os.environ.get(
    "CMLM_ANALYSIS_URL",
    "https://github.com/LumyQAQ/CMLM-reanalyze/blob/main/v4_cmlm_analysis_latest.md",
)

CSV_FILES = [
    "v4_pullback_candidates.csv",
    "v4_surge_range.csv",
    "v4_surge_trend.csv",
]


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_cmd(args: list[str], cwd: Path | None = None, timeout: int = 600) -> CommandResult:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line)
    with (LOG_DIR / "cmlm_auto_job.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_env() -> None:
    configured = os.environ.get("CMLM_ENV", "").strip()
    paths = [Path(configured).expanduser()] if configured else ENV_FILES
    for path in paths:
        load_env_file(path)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def is_trading_day(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    today = now.strftime("%Y-%m-%d")
    for path in [CMLM_DIR / "cn_market_holidays.txt", REPO_ROOT / "cn_market_holidays.txt"]:
        if not path.exists():
            continue
        holidays = {
            line.strip().split("#", 1)[0].strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        if today in holidays:
            return False
    return True


def infer_slot(now: datetime) -> str:
    slots = {
        "1135": 11 * 60 + 35,
        "1430": 14 * 60 + 30,
        "1505": 15 * 60 + 5,
    }
    minutes = now.hour * 60 + now.minute
    slot, distance = min(slots.items(), key=lambda item: abs(minutes - item[1]))
    if distance > 45:
        return now.strftime("%H%M")
    return slot


def run_engine(skip_engine: bool) -> None:
    if skip_engine:
        log("skip engine run")
        return
    log(f"running engine: {ENGINE_PATH}")
    result = run_cmd([sys.executable, str(ENGINE_PATH)], cwd=CMLM_DIR, timeout=900)
    if result.stdout.strip():
        log("engine stdout:\n" + result.stdout.strip()[-4000:])
    if result.stderr.strip():
        log("engine stderr:\n" + result.stderr.strip()[-4000:])
    if result.returncode != 0:
        raise RuntimeError(f"engine failed with code {result.returncode}")


def read_csv(name: str) -> pd.DataFrame:
    path = CMLM_DIR / name
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"代码": str})
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.extract(r"(\d+)")[0].str.zfill(6)
    if "名称" in df.columns:
        df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)].copy()
    return df


def sector_aggregate(trend: pd.DataFrame, range_df: pd.DataFrame, pullback: pd.DataFrame) -> pd.DataFrame:
    if trend.empty or "板块" not in trend.columns:
        return pd.DataFrame()

    trend_g = trend.groupby("板块").agg(
        trend_count=("代码", "count"),
        trend_amt=("今日成交额(亿)", "sum"),
        trend_ratio_max=("增量倍数", "max"),
        trend_ratio_mean=("增量倍数", "mean"),
        trend_pct_mean=("涨跌幅(%)", "mean"),
        trend_pct_max=("涨跌幅(%)", "max"),
        limit_count=("涨跌幅(%)", lambda s: (s >= 9.8).sum()),
        strong_count=("涨跌幅(%)", lambda s: (s >= 5).sum()),
    )

    range_g = pd.DataFrame()
    if not range_df.empty and "板块" in range_df.columns:
        range_g = range_df.groupby("板块").agg(
            range_count=("代码", "count"),
            range_amt=("今日成交额(亿)", "sum"),
            range_ratio_max=("增量倍数", "max"),
        )

    pull_g = pd.DataFrame()
    if not pullback.empty and "板块" in pullback.columns:
        pull_g = pullback.groupby("板块").agg(
            pull_count=("代码", "count"),
            pull_amt=("今日成交额(亿)", "sum"),
        )

    agg = trend_g.join(range_g, how="outer").join(pull_g, how="outer").fillna(0)
    expected_cols = [
        "trend_count",
        "trend_amt",
        "trend_ratio_max",
        "trend_ratio_mean",
        "trend_pct_mean",
        "trend_pct_max",
        "limit_count",
        "strong_count",
        "range_count",
        "range_amt",
        "range_ratio_max",
        "pull_count",
        "pull_amt",
    ]
    agg = agg.reindex(columns=expected_cols, fill_value=0)
    agg["score"] = (
        agg["trend_count"] * 2.0
        + agg["strong_count"] * 1.0
        + agg["limit_count"] * 1.5
        + agg["range_count"] * 3.0
        + agg["pull_count"] * 0.8
        + agg["trend_amt"].clip(upper=120) / 20
        + agg["range_amt"].clip(upper=80) / 25
        + agg["trend_ratio_max"].clip(upper=8) / 2
    )
    return agg.sort_values("score", ascending=False)


def row_stock(row: pd.Series) -> str:
    return f"{row['名称']}({row['代码']})"


def multi_day_change_text(row: pd.Series) -> str:
    parts = []
    for col, label in [("3日涨幅(%)", "3日"), ("5日涨幅(%)", "5日")]:
        value = row.get(col)
        if value is None or pd.isna(value):
            continue
        try:
            parts.append(f"{label}{float(value):+g}%")
        except (TypeError, ValueError):
            parts.append(f"{label}{value}")
    return "，" + "，".join(parts) if parts else ""


def stock_line(row: pd.Series) -> str:
    label = row.get("逻辑标签", "")
    return (
        f"{row_stock(row)} +{row['涨跌幅(%)']}%，"
        f"量{row['增量倍数']}x，额{row['今日成交额(亿)']}亿"
        + multi_day_change_text(row)
        + (f"，{label}" if label else "")
    )


def extreme_phrase(agg: pd.DataFrame) -> str:
    if agg.empty:
        return "数据为空，只能先管住手"

    top = agg.iloc[0]
    lead_score = float(top.get("score", 0))
    strong_count = float(top.get("strong_count", 0))
    limit_count = float(top.get("limit_count", 0))
    trend_count = float(top.get("trend_count", 0))

    if limit_count >= 10 or (strong_count >= 30 and trend_count >= 40):
        return "偏热，已经接近小极端"
    if lead_score >= 150 and strong_count >= 20:
        return "主线已经立起来，但还没到大极端"
    if lead_score >= 90:
        return "有交易温度，但仍属于可观察区"
    return "还在普通波动区，不宜把日内噪音当成极端"


def market_truth_lines(first: str, second: str, third: str) -> list[str]:
    return [
        f"当前主战场我先看 **{first}**。" if first else "当前没有足够清晰的主战场。",
        f"第二梯队是 **{second}**。" if second else "第二梯队暂时没有足够清晰的替代主线。",
        f"第三观察线是 **{third}**。" if third else "第三观察线暂时不成型。",
        "双龙不是单票强，而是“龙头板块 + 龙头股”同时成立。",
    ]


def risk_lines(first: str) -> list[str]:
    return [
        f"- 市场如果原来只盯着单票情绪，现在真正的预期差是 **{first}** 能不能把板块合力带出来。" if first else "- 市场如果没有清晰主线，就不要把尾盘噪音当预期差。",
        "- 如果后面只剩弹性先锋冲，中军和结构突破不跟，那就是围猎，不是主线。",
        "- 把单票涨得快当成龙头板块，是散户最容易犯的错。",
        "- 把弹性先锋当总龙，也是散户最容易犯的错。",
        "- 把已经走出来的强势，当成下一根就必须继续加速，同样是散户最容易犯的错。",
    ]


def observation_lines(first: str, second: str, third: str) -> list[str]:
    lines = [
        f"- 如果下一轮数据里 **{first}** 继续扩散，且中军不掉队，它才是真主战场。",
        "- 如果只剩弹性先锋冲，中军和结构突破不跟，就是短线围猎，不是稳定双龙。",
    ]
    if second:
        lines.append(f"- 如果 **{second}** 反超主线，说明预期差在切换，不能用上一轮结论硬扛。")
    if third:
        lines.append(f"- 如果 **{third}** 也加入扩散，主线会更硬，才有资格谈更大的极端。")
    lines.append("- 不极端不交易；如果条件没有出现，就不把热闹当成机会。")
    return lines


def top_sector_details(
    sectors: list[str],
    trend: pd.DataFrame,
    range_df: pd.DataFrame,
    pullback: pd.DataFrame,
) -> dict[str, dict[str, list[str]]]:
    details: dict[str, dict[str, list[str]]] = {}
    for sector in sectors:
        t = trend[trend["板块"] == sector].copy()
        r = range_df[range_df["板块"] == sector].copy() if not range_df.empty else pd.DataFrame()
        p = pullback[pullback["板块"] == sector].copy() if not pullback.empty else pd.DataFrame()

        by_amount = t.sort_values("今日成交额(亿)", ascending=False).head(5)
        by_ratio = t.sort_values(["增量倍数", "今日成交额(亿)"], ascending=False).head(5)
        range_top = r.sort_values("增量倍数", ascending=False).head(5) if not r.empty else pd.DataFrame()
        pull_top = p.head(5) if not p.empty else pd.DataFrame()

        details[sector] = {
            "amount_leaders": [stock_line(row) for _, row in by_amount.iterrows()],
            "elastic_leaders": [stock_line(row) for _, row in by_ratio.iterrows()],
            "range_breakouts": [
                f"{row_stock(row)} +{row['涨跌幅(%)']}%，{row['突破类型']}，量{row['增量倍数']}x，额{row['今日成交额(亿)']}亿{multi_day_change_text(row)}"
                for _, row in range_top.iterrows()
            ],
            "pullbacks": [
                f"{row_stock(row)} {row['今日涨幅(%)']}%，{row['回踩天数']}，量/爆{row['今日量/爆发量']}，额{row['今日成交额(亿)']}亿{multi_day_change_text(row)}"
                for _, row in pull_top.iterrows()
            ],
        }
    return details


def empty_analysis(slot: str, generated_at: str) -> tuple[str, dict[str, Any]]:
    text = (
        f"## CMLM 龙头板块龙头股复盘（{slot}）\n\n"
        f"- 生成时间：{generated_at}\n"
        "- 口径：CMLM 三模数据，只做复盘和观察条件，不构成买卖建议。\n\n"
        "### 市场真相\n\n"
        "三模数据为空。没有数据，就先管住手。\n\n"
        "### 极端程度\n\n"
        "数据为空，只能先按不极端处理。\n\n"
        "### 观察条件\n\n"
        "- 等数据恢复后再看板块合力。\n"
        "- 不极端不交易。\n"
    )
    return text, {"type": "leader", "slot": slot, "generated_at": generated_at, "summary": text}


def make_leader_analysis(
    slot: str,
    generated_at: str,
    trend: pd.DataFrame,
    range_df: pd.DataFrame,
    pullback: pd.DataFrame,
) -> tuple[str, dict[str, Any]]:
    agg = sector_aggregate(trend, range_df, pullback)
    if agg.empty:
        return empty_analysis(slot, generated_at)

    top = agg.head(8).round(2)
    top_sectors = list(top.index[:5])
    details = top_sector_details(top_sectors, trend, range_df, pullback)
    first = top_sectors[0]
    second = top_sectors[1] if len(top_sectors) > 1 else ""
    third = top_sectors[2] if len(top_sectors) > 2 else ""

    lines = [
        f"## CMLM 龙头板块龙头股复盘（{slot}）",
        "",
        f"- 生成时间：{generated_at}",
        "- 口径：CMLM 三模数据，只做复盘和观察条件，不构成买卖建议。",
        "",
        "### 市场真相",
        "",
    ]
    lines += market_truth_lines(first, second, third)
    lines += [
        "",
        "### 极端程度",
        "",
        f"按当前板块扩散和强势股分布，我更愿意把它看成：**{extreme_phrase(agg)}**。",
        "大极端大交易，小极端小交易，不极端不交易。",
        "",
        "### 资金性质",
        "",
        f"- 中军/承载：看板块里最能扛成交额的那批票，当前代表是 **{first}** 的核心票。",
        "- 弹性先锋：看涨得最快、换手最高的那批票，容易出气势，也最容易把人带进围猎。",
        "- 结构突破：看突破位能不能站住，站不住就只是热闹，不是立木。",
        "",
        "### 预期差",
        "",
    ]
    lines += risk_lines(first)[:2]
    lines += ["", "### 散户最容易犯的错", ""]
    lines += risk_lines(first)[2:]
    lines += [
        "",
        "### 板块强度表",
        "",
        "| 板块 | 综合分 | 趋势股 | 强势股 | 涨停/近涨停 | 趋势成交额 | 区间突破 | 回踩候选 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sector, row in top.iterrows():
        lines.append(
            f"| {sector} | {row['score']:.2f} | {int(row['trend_count'])} | {int(row['strong_count'])} | "
            f"{int(row['limit_count'])} | {row['trend_amt']:.2f}亿 | {int(row['range_count'])} | {int(row['pull_count'])} |"
        )

    lines += ["", "### 双龙拆解", ""]
    for sector in top_sectors:
        detail = details[sector]
        lines.append(f"**{sector}**")
        if detail["amount_leaders"]:
            lines.append("- 中军/承载：" + "；".join(detail["amount_leaders"][:4]))
        if detail["elastic_leaders"]:
            lines.append("- 弹性先锋：" + "；".join(detail["elastic_leaders"][:4]))
        if detail["range_breakouts"]:
            lines.append("- 结构突破：" + "；".join(detail["range_breakouts"][:3]))
        if detail["pullbacks"]:
            lines.append("- 龙回头候选：" + "；".join(detail["pullbacks"][:3]))
        lines.append("")

    lines += ["### 观察条件", ""]
    lines += observation_lines(first, second, third)

    text = "\n".join(lines) + "\n"
    payload = {
        "type": "leader",
        "slot": slot,
        "generated_at": generated_at,
        "top_sectors": top.reset_index().rename(columns={"index": "板块"}).to_dict(orient="records"),
        "details": details,
        "summary": text,
    }
    return text, payload


def make_watchlist(
    slot: str,
    generated_at: str,
    trend: pd.DataFrame,
    range_df: pd.DataFrame,
    pullback: pd.DataFrame,
) -> tuple[str, dict[str, Any]]:
    agg = sector_aggregate(trend, range_df, pullback)
    top_sectors = list(agg.head(6).index) if not agg.empty else []
    trend_pool = trend[trend["板块"].isin(top_sectors)].copy() if top_sectors and not trend.empty else trend.copy()
    right_side = trend_pool[
        (trend_pool["涨跌幅(%)"] >= 5.0)
        & (trend_pool["增量倍数"] >= 1.5)
        & (trend_pool["今日成交额(亿)"] >= 2.0)
    ].sort_values(["今日成交额(亿)", "增量倍数"], ascending=False).head(10) if not trend_pool.empty else pd.DataFrame()

    breakout = range_df[range_df["板块"].isin(top_sectors)].copy() if top_sectors and not range_df.empty else range_df.copy()
    if not breakout.empty:
        breakout = breakout.sort_values(["增量倍数", "今日成交额(亿)"], ascending=False).head(8)

    pb = pullback.copy()
    if not pb.empty:
        shrink = pd.to_numeric(pb["今日量/爆发量"].astype(str).str.extract(r"([0-9.]+)")[0], errors="coerce")
        pb = pb.assign(_shrink=shrink)
        pb = pb[(pb["_shrink"] <= 55) & (pb["今日成交额(亿)"] >= 0.8)].sort_values("_shrink").head(8)

    first = top_sectors[0] if top_sectors else ""
    second = top_sectors[1] if len(top_sectors) > 1 else ""
    third = top_sectors[2] if len(top_sectors) > 2 else ""
    lines = [
        f"## CMLM 14:30 尾盘观察池（{slot}）",
        "",
        f"- 生成时间：{generated_at}",
        "- 口径：尾盘观察清单，不是买入指令；必须结合盘口、封单、板块扩散和个人仓位。",
        "",
        "### 市场真相",
        "",
        "尾盘不是追涨的借口，是检验主线真假的地方。",
    ]
    lines += market_truth_lines(first, second, third)
    lines += [
        "",
        "### 极端程度",
        "",
        f"按当前尾盘条件，我更愿意把它看成：**{extreme_phrase(agg)}**。",
        "大极端大交易，小极端小交易，不极端不交易。",
        "",
        "### 右侧主升接力",
        "",
    ]
    if right_side.empty:
        lines.append("- 暂无满足量价条件的右侧接力标的。")
    else:
        for _, row in right_side.iterrows():
            lines.append(f"- {stock_line(row)}；板块：{row['板块']}。")

    lines += ["", "### 区间突破观察", ""]
    if breakout.empty:
        lines.append("- 暂无区间突破观察标的。")
    else:
        for _, row in breakout.iterrows():
            lines.append(
                f"- {row_stock(row)} +{row['涨跌幅(%)']}%，{row['突破类型']}，量{row['增量倍数']}x，额{row['今日成交额(亿)']}亿{multi_day_change_text(row)}；板块：{row['板块']}。"
            )

    lines += ["", "### 龙回头低吸观察", ""]
    if pb.empty:
        lines.append("- 暂无极致缩量回踩观察标的。")
    else:
        for _, row in pb.iterrows():
            lines.append(
                f"- {row_stock(row)} {row['今日涨幅(%)']}%，{row['回踩天数']}，量/爆{row['今日量/爆发量']}，额{row['今日成交额(亿)']}亿{multi_day_change_text(row)}；板块：{row['板块']}。"
            )

    lines += ["", "### 资金性质", "", "- 右侧接力看承接，不看一根线冲高。", "- 区间突破看站稳，不看假突破。", "- 龙回头看缩量回踩，不看破位后的自我安慰。", "", "### 预期差", ""]
    lines += risk_lines(first)[:2]
    lines += ["", "### 散户最容易犯的错", ""]
    lines += risk_lines(first)[2:]
    lines += ["", "### 观察条件", ""]
    lines += observation_lines(first, second, third) if first else ["- 不极端不交易。"]

    text = "\n".join(lines) + "\n"
    payload = {
        "type": "watchlist",
        "slot": slot,
        "generated_at": generated_at,
        "top_sectors": top_sectors,
        "right_side": right_side.to_dict(orient="records") if not right_side.empty else [],
        "breakout": breakout.to_dict(orient="records") if not breakout.empty else [],
        "pullback": pb.drop(columns=["_shrink"], errors="ignore").to_dict(orient="records") if not pb.empty else [],
        "summary": text,
    }
    return text, payload


def write_analysis(slot: str, trend: pd.DataFrame, range_df: pd.DataFrame, pullback: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if slot == "1430":
        text, payload = make_watchlist(slot, generated_at, trend, range_df, pullback)
    else:
        text, payload = make_leader_analysis(slot, generated_at, trend, range_df, pullback)
    OUTPUT_MD.write_text(text, encoding="utf-8")
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"analysis generated: {OUTPUT_MD.name}, {OUTPUT_JSON.name}")
    return text, payload


def http_post_json(url: str, payload: dict[str, Any], timeout: int = 25) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def http_post_form(url: str, payload: dict[str, str], timeout: int = 25) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def trim_message(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n...内容过长已截断，全文见 GitHub 同步文件。"
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def notification_title(slot: str) -> str:
    names = {
        "1135": "CMLM 11:35 龙头板块龙头股",
        "1430": "CMLM 14:30 尾盘观察池",
        "1505": "CMLM 15:05 收盘复盘",
    }
    return names.get(slot, f"CMLM {slot} 复盘")


def send_notification(slot: str, text: str, disabled: bool) -> None:
    if disabled:
        log("notification disabled by --no-notify")
        return
    provider = os.environ.get("CMLM_NOTIFY_PROVIDER", "").strip().lower()
    if provider in {"", "0", "off", "false", "none"}:
        log("notification disabled; set CMLM_NOTIFY_PROVIDER to enable")
        return

    title = notification_title(slot)
    limit = int(os.environ.get("CMLM_NOTIFY_MAX_CHARS", "7000"))
    body = trim_message(text.strip() + f"\n\n---\nGitHub：{GITHUB_ANALYSIS_URL}", limit)
    if env_bool("CMLM_NOTIFY_DRY_RUN"):
        dry_run_dir = CMLM_DIR / "notify_dryrun"
        dry_run_dir.mkdir(parents=True, exist_ok=True)
        path = dry_run_dir / f"{provider}_{slot}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        log(f"notification dry-run written: {path}")
        return

    if provider == "pushplus":
        token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("missing PUSHPLUS_TOKEN")
        payload = {"token": token, "title": title, "content": body, "template": "markdown", "channel": "wechat"}
        topic = os.environ.get("PUSHPLUS_TOPIC", "").strip()
        if topic:
            payload["topic"] = topic
        result = http_post_json("https://www.pushplus.plus/send", payload)
    elif provider == "serverchan":
        sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
        if not sendkey:
            raise RuntimeError("missing SERVERCHAN_SENDKEY")
        result = http_post_form(f"https://sctapi.ftqq.com/{sendkey}.send", {"title": title, "desp": body})
    elif provider == "wecom":
        webhook = os.environ.get("WECOM_BOT_WEBHOOK", "").strip()
        if not webhook:
            raise RuntimeError("missing WECOM_BOT_WEBHOOK")
        result = http_post_json(webhook, {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n\n{body}"}})
    else:
        raise RuntimeError(f"unsupported CMLM_NOTIFY_PROVIDER: {provider}")
    log(f"notification response via {provider}: {str(result)[:300]}")


def ensure_publish_repo(publish_dir: Path) -> None:
    if not publish_dir.exists():
        log(f"cloning publish repo: {REMOTE_URL} -> {publish_dir}")
        publish_dir.parent.mkdir(parents=True, exist_ok=True)
        result = run_cmd(["git", "clone", "--depth", "1", REMOTE_URL, str(publish_dir)], timeout=600)
    else:
        result = run_cmd(["git", "pull", "--rebase", "origin", "main"], cwd=publish_dir, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_publish_snapshot(publish_dir: Path, sources: list[Path]) -> None:
    for source in sources:
        target = publish_dir / source.name
        if not source.exists():
            raise RuntimeError(f"missing source artifact: {source}")
        if not target.exists():
            raise RuntimeError(f"missing published artifact: {target}")
        if file_sha256(source) != file_sha256(target):
            raise RuntimeError(f"artifact mismatch after copy: {source.name}")


def publish(slot: str, no_push: bool) -> None:
    publish_dir = DRYRUN_PUBLISH_DIR if no_push else PUBLISH_DIR
    ensure_publish_repo(publish_dir)

    files = [CMLM_DIR / name for name in CSV_FILES] + [OUTPUT_MD, OUTPUT_JSON]
    existing = [path for path in files if path.exists()]
    for source in existing:
        shutil.copy2(source, publish_dir / source.name)
    verify_publish_snapshot(publish_dir, existing)

    add = run_cmd(["git", "add", "-f", *[path.name for path in existing]], cwd=publish_dir, timeout=120)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or add.stdout.strip())
    status = run_cmd(["git", "status", "--porcelain", "--", *[path.name for path in existing]], cwd=publish_dir)
    if not status.stdout.strip():
        log("no publish changes")
        return

    message = f"chore: update CMLM {slot} scan {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    commit = run_cmd(["git", "commit", "-m", message], cwd=publish_dir, timeout=120)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip())
    if no_push:
        log(f"no-push enabled; committed in dry-run repo only: {publish_dir}")
        return

    push = run_cmd(["git", "push", "origin", "HEAD:main"], cwd=publish_dir, timeout=300)
    if push.returncode != 0:
        raise RuntimeError(push.stderr.strip() or push.stdout.strip())
    fetch = run_cmd(["git", "fetch", "origin", "main"], cwd=publish_dir, timeout=300)
    if fetch.returncode != 0:
        raise RuntimeError(fetch.stderr.strip() or fetch.stdout.strip())
    remote = run_cmd(["git", "rev-parse", "FETCH_HEAD"], cwd=publish_dir, timeout=60)
    local = run_cmd(["git", "rev-parse", "HEAD"], cwd=publish_dir, timeout=60)
    if remote.stdout.strip() != local.stdout.strip():
        raise RuntimeError("push verification failed: origin/main does not match local HEAD")
    log("published to origin/main")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=["1135", "1430", "1505"], help="Scheduled slot.")
    parser.add_argument("--auto-slot", action="store_true", help="Infer slot from current local time.")
    parser.add_argument("--force", action="store_true", help="Run even outside trading day.")
    parser.add_argument("--skip-engine", action="store_true", help="Do not run v4_engine.py.")
    parser.add_argument("--no-publish", action="store_true", help="Do not copy/commit/push publish files.")
    parser.add_argument("--no-push", action="store_true", help="Commit into publish clone but do not push.")
    parser.add_argument("--no-notify", action="store_true", help="Do not send notification.")
    args = parser.parse_args()

    now = datetime.now()
    slot = args.slot or (infer_slot(now) if args.auto_slot else now.strftime("%H%M"))
    if slot not in {"1135", "1430", "1505"}:
        raise SystemExit(f"Unsupported slot inferred: {slot}; pass --slot 1135/1430/1505")
    if not args.force and not is_trading_day(now):
        log(f"skip non-trading day: {now.strftime('%Y-%m-%d')}")
        return 0

    log(f"start CMLM auto job slot={slot}")
    try:
        run_engine(skip_engine=args.skip_engine)
        trend = read_csv("v4_surge_trend.csv")
        range_df = read_csv("v4_surge_range.csv")
        pullback = read_csv("v4_pullback_candidates.csv")
        text, _payload = write_analysis(slot, trend, range_df, pullback)
        send_notification(slot, text, disabled=args.no_notify)
        if args.no_publish:
            log("no-publish enabled; skip publish step")
        else:
            publish(slot, no_push=args.no_push)
        log(f"finish CMLM auto job slot={slot}")
        return 0
    except Exception as exc:
        log(f"ERROR slot={slot}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

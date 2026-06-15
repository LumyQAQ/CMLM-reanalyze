# CMLM 量价三模复盘系统 V4.6

CMLM（城门立木）是一套 A 股量价扫描与 Streamlit 看板系统。它把本地行情扫描、GitHub 数据同步和云端看板拆开：本地负责拉取 mootdx 行情并生成 CSV，云端只读取已生成的数据文件。

本项目只做复盘、观察条件和投研辅助，不构成任何买卖建议、收益承诺或投资顾问服务。

## 核心模块

- `v4_engine.py`：本地扫描引擎，生成三类结果池。
- `v4_surge_board.py`：三模量价复盘主看板。
- `v4_dashboard.py`：RRG 四象限板块雷达看板。
- `scripts/cmlm_auto_job.py`：定时运行、生成 Markdown/JSON 复盘、发布到 GitHub、可选推送通知。
- `tests/`：不连接真实行情源的单元测试。

## 三类结果池

| 文件 | 含义 |
|---|---|
| `v4_surge_trend.csv` | 右侧趋势：成交额放量、趋势接力、二波起涨 |
| `v4_surge_range.csv` | 右侧结构：横盘后放量突破区间前高 |
| `v4_pullback_candidates.csv` | 左侧低吸：突破后缩量回踩且未破关键防线 |

三类 CSV 均包含 `3日涨幅(%)`、`5日涨幅(%)`，保证看板、自动报告和数据产物口径一致。

## 安装

```bash
git clone https://github.com/LumyQAQ/CMLM-reanalyze.git
cd CMLM-reanalyze
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

根目录需要存在 `stock_to_sector.csv`，字段至少包含“代码、名称、行业”。当前仓库内置文件为 UTF-16 编码，引擎会按 `utf-16 / gbk / utf-8-sig / utf-8` 顺序尝试读取。

## 手动运行

```bash
python v4_engine.py
streamlit run v4_surge_board.py
```

旧 RRG 看板可用：

```bash
streamlit run v4_dashboard.py
```

## 自动任务

生成报告但不跑行情、不发布：

```bash
python scripts/cmlm_auto_job.py --slot 1505 --force --skip-engine --no-publish --no-notify
```

完整流程：

```bash
python scripts/cmlm_auto_job.py --auto-slot
```

常用 slot：

| slot | 用途 |
|---|---|
| `1135` | 上午收盘复盘 |
| `1430` | 尾盘观察池 |
| `1505` | 收盘复盘 |

首次配置通知时复制样例：

```bash
cp scripts/cmlm_auto.env.example scripts/cmlm_auto.env
```

支持 `pushplus`、`serverchan`、`wecom`。先设置 `CMLM_NOTIFY_DRY_RUN=1` 做本地预演，确认内容后再关闭 dry-run。

## GitHub 发布

自动任务默认把以下文件复制到干净的发布 clone，再提交到 `origin/main`：

- `v4_pullback_candidates.csv`
- `v4_surge_range.csv`
- `v4_surge_trend.csv`
- `v4_cmlm_analysis_latest.md`
- `v4_cmlm_analysis_latest.json`

先 dry-run：

```bash
python scripts/cmlm_auto_job.py --slot 1505 --force --skip-engine --no-push --no-notify
```

发布前脚本会校验复制后的文件 SHA-256，避免把半写入或错误文件提交出去。

## 测试

```bash
python -m py_compile v4_engine.py v4_dashboard.py v4_surge_board.py test_kline.py scripts/cmlm_auto_job.py
python -m unittest discover -s tests
```

测试不会连接真实行情源。若后续要启用 GitHub Actions，可把上述两条命令放入 workflow；当前推送凭据需要带 `workflow` scope 才能创建 `.github/workflows/*` 文件。

## 数据与提交约定

- 代码改动和数据刷新尽量分开提交。
- 数据刷新提交建议使用 `chore: update CMLM <slot> scan YYYY-MM-DD HH:MM`。
- 不要把 `logs/`、`notify_dryrun/`、本地 `.env` 文件提交到仓库。

## 风险边界

行情源可能超时、缺字段或返回空数据。引擎会打印失败批次和 K 线失败样本；如果核心数据不可用，脚本会退出非零状态，而不是静默生成看似正常的结果。

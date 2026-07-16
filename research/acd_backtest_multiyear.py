from __future__ import annotations

import dataclasses
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from acd_backtest import Config, backtest, metrics

NY = "America/New_York"
OUT = Path("artifacts_multiyear")
OUT.mkdir(exist_ok=True)
DATA_URL = (
    "https://raw.githubusercontent.com/TheSnowGuru/"
    "Stocks-Futures-Financial-Time-series-Tick-Bar-Data/main/"
    "commodities/gold/XAUUSD_M5.csv"
)


def load_xauusd() -> tuple[pd.DataFrame, pd.Series]:
    raw = pd.read_csv(DATA_URL, sep="\t")
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    raw["time"] = pd.to_datetime(raw["time"], utc=True, errors="coerce")
    raw = raw.dropna(subset=["time", "open", "high", "low", "close"])
    raw = raw.set_index("time").sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]

    # Repository documentation states timestamps are GMT. Daily ATR is built
    # on GMT calendar days, then shifted one completed day before use.
    daily = raw[["high", "low", "close"]].resample("1D").agg(
        {"high": "max", "low": "min", "close": "last"}
    ).dropna()
    prev_close = daily["close"].shift(1)
    tr = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    atr.index = pd.Index([x.date() for x in atr.index])

    intraday = raw[["open", "high", "low", "close", "volume"]].copy()
    intraday.index = intraday.index.tz_convert(NY)
    intraday["ema200"] = intraday["close"].ewm(span=200, adjust=False).mean()
    return intraday, atr.dropna()


def year_metrics(trades, year: int) -> dict[str, float]:
    return metrics([t for t in trades if int(t.date[:4]) == year])


def discovery_metrics(trades) -> dict[str, float]:
    return metrics([t for t in trades if int(t.date[:4]) <= 2021])


def main() -> None:
    intraday, atr = load_xauusd()
    years = sorted(set(intraday.index.year))

    sessions = [
        ("NY0930", "09:30", "09:45", "12:00", "15:00"),
        ("COMEX0820", "08:20", "08:35", "12:00", "15:00"),
    ]
    configs: list[Config] = []
    for sess, a_mult, c_mult, rr, enable_c, trend in itertools.product(
        sessions,
        [0.03, 0.05, 0.08, 0.10, 0.12],
        [0.00, 0.03, 0.05],
        [1.0, 1.5, 2.0],
        [False, True],
        ["none", "ema200"],
    ):
        sname, ostart, oend, acut, ccut = sess
        configs.append(
            Config(
                sname,
                ostart,
                oend,
                acut,
                ccut,
                a_mult,
                c_mult,
                "far",
                rr,
                enable_c,
                trend,
                cost_points=0.25,
            )
        )

    default_cfg = Config(
        "NY0930", "09:30", "09:45", "12:00", "15:00",
        0.10, 0.03, "far", 2.0, True, "none", cost_points=0.25
    )
    if default_cfg not in configs:
        configs.append(default_cfg)

    rows = []
    trade_cache: dict[Config, list] = {}
    for i, cfg in enumerate(configs, 1):
        trades = backtest(intraday, atr, cfg)
        trade_cache[cfg] = trades
        row = asdict(cfg)
        row.update({f"all_{k}": v for k, v in metrics(trades).items()})
        row.update({f"disc_{k}": v for k, v in discovery_metrics(trades).items()})
        for year in [2021, 2022, 2023]:
            row.update({f"y{year}_{k}": v for k, v in year_metrics(trades, year).items()})
        rows.append(row)
        if i % 60 == 0:
            print(f"processed {i}/{len(configs)}")

    results = pd.DataFrame(rows)
    results.to_csv(OUT / "acd_multiyear_grid.csv", index=False)

    eligible = results[
        (results.disc_trades >= 60)
        & np.isfinite(results.disc_profit_factor)
    ].copy()
    eligible["disc_score"] = (
        eligible.disc_avg_r.clip(lower=-1, upper=2)
        * np.sqrt(eligible.disc_trades)
        - 0.02 * eligible.disc_max_dd_r
    )
    top_discovery = eligible.sort_values(
        ["disc_score", "disc_profit_factor"], ascending=False
    ).head(30)
    top_discovery.to_csv(OUT / "acd_multiyear_top30_discovery.csv", index=False)

    # Strict robustness gate is evaluated only after discovery ranking is fixed.
    robust = top_discovery[
        (top_discovery.y2022_trades >= 20)
        & (top_discovery.y2023_trades >= 15)
        & (top_discovery.y2022_profit_factor > 1.0)
        & (top_discovery.y2023_profit_factor > 1.0)
        & (top_discovery.y2022_avg_r > 0.0)
        & (top_discovery.y2023_avg_r > 0.0)
    ].copy()
    robust.to_csv(OUT / "acd_multiyear_robust_candidates.csv", index=False)

    # Cost sensitivity is performed only after configurations are selected by
    # discovery data. It does not change the ranking.
    sensitivity_rows = []
    selected_records = top_discovery.head(10).to_dict("records")
    selected_cfgs: list[Config] = []
    for rec in selected_records:
        selected_cfgs.append(
            Config(
                rec["session_name"], rec["or_start"], rec["or_end"],
                rec["a_cutoff"], rec["c_cutoff"], rec["a_mult"],
                rec["c_mult"], rec["stop_mode"], rec["rr"],
                bool(rec["enable_c"]), rec["trend_filter"],
                cost_points=0.25,
                min_or_atr=rec["min_or_atr"],
                max_or_atr=rec["max_or_atr"],
                c_delay_bars=int(rec["c_delay_bars"]),
            )
        )
    selected_cfgs.append(default_cfg)

    for rank, cfg in enumerate(selected_cfgs, 1):
        for cost in [0.10, 0.25, 0.50, 1.00]:
            cost_cfg = dataclasses.replace(cfg, cost_points=cost)
            trades = backtest(intraday, atr, cost_cfg)
            row = {"discovery_rank": rank, **asdict(cost_cfg)}
            row.update({f"all_{k}": v for k, v in metrics(trades).items()})
            row.update({f"disc_{k}": v for k, v in discovery_metrics(trades).items()})
            for year in [2022, 2023]:
                row.update({f"y{year}_{k}": v for k, v in year_metrics(trades, year).items()})
            sensitivity_rows.append(row)
    sensitivity = pd.DataFrame(sensitivity_rows)
    sensitivity.to_csv(OUT / "acd_multiyear_cost_sensitivity.csv", index=False)

    default_trades = trade_cache[default_cfg]
    default_row = results[
        (results.session_name == default_cfg.session_name)
        & (results.a_mult == default_cfg.a_mult)
        & (results.c_mult == default_cfg.c_mult)
        & (results.rr == default_cfg.rr)
        & (results.enable_c == default_cfg.enable_c)
        & (results.trend_filter == default_cfg.trend_filter)
    ].iloc[0]

    cols = [
        "session_name", "a_mult", "c_mult", "rr", "enable_c",
        "trend_filter", "disc_trades", "disc_profit_factor", "disc_avg_r",
        "y2022_trades", "y2022_profit_factor", "y2022_avg_r",
        "y2023_trades", "y2023_profit_factor", "y2023_avg_r",
        "all_trades", "all_profit_factor", "all_avg_r", "all_max_dd_r",
    ]

    lines = [
        "# ACD XAUUSD Multi-year Backtest",
        "",
        "- Source: public XAUUSD M5 CSV; timestamps documented as GMT.",
        f"- Data: {intraday.index.min()} to {intraday.index.max()}",
        f"- Bars: {len(intraday):,}; calendar years: {years}",
        "- ATR: 14-day true range from completed GMT calendar days.",
        "- Round-trip cost in main grid: 0.25 gold points per trade.",
        "- Conservative execution: dual A breakout bar skipped; stop wins when stop and target touch in the same M5 bar; C entry delayed by one bar after B stop.",
        "- Discovery period: through 2021; 2022 and 2023 are untouched annual validations.",
        "",
        "## Default parameters",
        "",
        "NY 09:30–09:45 OR; A=0.10 ATR; C=0.03 ATR; far-side B/D stop; 2R; C enabled; no trend filter.",
        "",
        f"```json\n{json.dumps({k: default_row[k] for k in cols if k in default_row}, ensure_ascii=False, indent=2, default=float)}\n```",
        "",
        "## Top 10 ranked only on discovery period",
        "",
        top_discovery.head(10)[cols].to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Strict robust candidates",
        "",
        f"Count: {len(robust)}",
        "",
        robust[cols].to_markdown(index=False, floatfmt=".3f") if not robust.empty else "None passed.",
        "",
        "## Interpretation rule",
        "",
        "A configuration is not promoted merely because full-sample PF is positive. It must first rank on discovery data and then remain positive in both 2022 and 2023 under costs.",
    ]
    (OUT / "acd_multiyear_summary.md").write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame([dataclasses.asdict(t) for t in default_trades]).to_csv(
        OUT / "acd_multiyear_default_trades.csv", index=False
    )
    (OUT / "acd_multiyear_data_info.json").write_text(
        json.dumps(
            {
                "start": intraday.index.min().isoformat(),
                "end": intraday.index.max().isoformat(),
                "bars": int(len(intraday)),
                "years": years,
                "configs": len(configs),
                "robust_candidates": int(len(robust)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print((OUT / "acd_multiyear_summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

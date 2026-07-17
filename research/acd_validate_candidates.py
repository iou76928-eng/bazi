from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from acd_backtest import Config, backtest, metrics
from acd_backtest_multiyear import load_xauusd

OUT = Path("artifacts_candidates")
OUT.mkdir(exist_ok=True)


def ym(trades, year):
    return metrics([t for t in trades if int(t.date[:4]) == year])


def cfg(a, c, rr, ema=True, c_on=True, session="NY0930"):
    if session == "NY0930":
        times = ("09:30", "09:45", "12:00", "15:00")
    else:
        times = ("08:20", "08:35", "12:00", "15:00")
    return Config(
        session, *times, a, c, "far", rr, c_on,
        "ema200" if ema else "none", cost_points=0.25
    )


def main():
    intraday, atr = load_xauusd()
    candidates = [
        ("default", cfg(0.10, 0.03, 2.0, ema=False, c_on=True)),
        ("N03_C00_R1", cfg(0.03, 0.00, 1.0)),
        ("N05_C00_R1", cfg(0.05, 0.00, 1.0)),
        ("N05_C03_R1", cfg(0.05, 0.03, 1.0)),
        ("N08_C00_R15", cfg(0.08, 0.00, 1.5)),
        ("N08_C03_R15", cfg(0.08, 0.03, 1.5)),
        ("N08_C05_R15", cfg(0.08, 0.05, 1.5)),
        ("N10_C00_R15", cfg(0.10, 0.00, 1.5)),
        ("N10_C03_R15", cfg(0.10, 0.03, 1.5)),
        ("N10_C05_R15", cfg(0.10, 0.05, 1.5)),
        ("control_N08_noEMA", cfg(0.08, 0.03, 1.5, ema=False)),
        ("control_N08_noC", cfg(0.08, 0.03, 1.5, ema=True, c_on=False)),
        ("control_COMEX", cfg(0.08, 0.03, 1.5, ema=True, c_on=True, session="COMEX0820")),
    ]

    rows = []
    for name, config in candidates:
        trades = backtest(intraday, atr, config)
        row = {"name": name, **asdict(config)}
        row.update({f"all_{k}": v for k, v in metrics(trades).items()})
        for year in [2020, 2021, 2022, 2023]:
            row.update({f"y{year}_{k}": v for k, v in ym(trades, year).items()})
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "candidate_validation.csv", index=False)
    cols = [
        "name", "all_trades", "all_win_rate", "all_profit_factor", "all_avg_r", "all_max_dd_r",
        "y2021_trades", "y2021_profit_factor", "y2021_avg_r",
        "y2022_trades", "y2022_profit_factor", "y2022_avg_r",
        "y2023_trades", "y2023_profit_factor", "y2023_avg_r",
    ]
    passed = df[
        (df.y2021_trades >= 20) & (df.y2022_trades >= 20) & (df.y2023_trades >= 15)
        & (df.y2021_profit_factor > 1) & (df.y2022_profit_factor > 1) & (df.y2023_profit_factor > 1)
        & (df.y2021_avg_r > 0) & (df.y2022_avg_r > 0) & (df.y2023_avg_r > 0)
    ]
    lines = [
        "# ACD Candidate Multi-year Validation",
        "",
        f"Data: {intraday.index.min()} to {intraday.index.max()}, {len(intraday):,} M5 bars.",
        "Cost: 0.25 gold points round trip; same-bar stop/target is counted as stop.",
        "",
        "## Results",
        "",
        df[cols].to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Passed every full validation year",
        "",
        f"Count: {len(passed)}",
        "",
        passed[cols].to_markdown(index=False, floatfmt=".3f") if len(passed) else "None passed.",
    ]
    (OUT / "candidate_validation.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "candidate_data_info.json").write_text(json.dumps({
        "start": intraday.index.min().isoformat(),
        "end": intraday.index.max().isoformat(),
        "bars": len(intraday),
        "passed": len(passed),
    }, indent=2), encoding="utf-8")
    print((OUT / "candidate_validation.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

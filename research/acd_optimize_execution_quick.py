from __future__ import annotations

import itertools
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from acd_backtest import metrics
from acd_backtest_multiyear import load_xauusd
from acd_optimize_execution import ExecConfig, backtest, pm

OUT = Path("artifacts_execution_quick")
OUT.mkdir(exist_ok=True)


def main():
    intraday, atr = load_xauusd()
    configs = [
        ExecConfig(0.05, em, sm, rr, be, fx)
        for em, sm, rr, be, fx in itertools.product(
            ["touch", "close1", "close2", "retest"],
            ["far", "mid", "signal_low"],
            [0.75, 1.0, 1.25, 1.5],
            [None, 0.75, 1.0],
            ["12:00", "16:00"],
        )
    ]
    rows = []
    for cfg in configs:
        trades = backtest(intraday, atr, cfg)
        row = asdict(cfg)
        row.update({f"disc_{k}": v for k, v in pm(trades, None, 2021).items()})
        row.update({f"y2022_{k}": v for k, v in pm(trades, 2022, 2022).items()})
        row.update({f"y2023_{k}": v for k, v in pm(trades, 2023, 2023).items()})
        row.update({f"all_{k}": v for k, v in metrics(trades).items()})
        rows.append(row)

    df = pd.DataFrame(rows)
    df["disc_score"] = (
        df.disc_avg_r.clip(lower=-1, upper=2) * np.sqrt(df.disc_trades.clip(lower=0))
        - 0.025 * df.disc_max_dd_r
    )
    df.to_csv(OUT / "quick_grid.csv", index=False)
    ranked = df[
        (df.disc_trades >= 40)
        & np.isfinite(df.disc_profit_factor)
        & (df.disc_profit_factor > 1)
        & (df.disc_avg_r > 0)
    ].sort_values(["disc_score", "disc_profit_factor"], ascending=False)
    robust = ranked[
        (ranked.y2022_trades >= 15)
        & (ranked.y2023_trades >= 12)
        & (ranked.y2022_profit_factor > 1)
        & (ranked.y2023_profit_factor > 1)
        & (ranked.y2022_avg_r > 0)
        & (ranked.y2023_avg_r > 0)
    ]
    cols = [
        "entry_mode", "stop_mode", "rr", "breakeven_r", "force_exit",
        "disc_trades", "disc_profit_factor", "disc_avg_r", "disc_max_dd_r",
        "y2022_trades", "y2022_profit_factor", "y2022_avg_r",
        "y2023_trades", "y2023_profit_factor", "y2023_avg_r",
        "all_trades", "all_win_rate", "all_profit_factor", "all_avg_r", "all_max_dd_r",
    ]
    robust.to_csv(OUT / "quick_validation_positive.csv", index=False)
    lines = [
        "# ACD Quick Execution Test",
        "",
        "Fixed: Tue-Fri, long only, A=0.05 ATR, EMA200, no C, OR/ATR 0.05-0.35.",
        "",
        "## Top discovery-ranked configurations",
        "",
        ranked.head(15)[cols].to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Positive in both 2022 and 2023",
        "",
        f"Count: {len(robust)}",
        "",
        robust.head(30)[cols].to_markdown(index=False, floatfmt=".3f") if len(robust) else "None.",
    ]
    (OUT / "quick_report.md").write_text("\n".join(lines), encoding="utf-8")
    print((OUT / "quick_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

# CFB player-projection tuning — 2026-10-02

Grid: usage half-life {2, 3, 4, 6} x prior-season weight {0.10, 0.25, 0.50} x
starting-QB attempt floor {0.80, 0.88, 0.94}; chosen on 2024, scored on 2025
(`scripts/tune_player_props.py`, 2023-2025 ESPN box scores).

**Result: no change shipped.** The first run picked half-life 2 / prior weight
0.50 / floor 0.80 on a mean MAE-ratio-to-naive of 0.9659 vs 0.9698 shipped — but
the naive baseline read the same half-life being tuned, so the ratio moved with
the candidate. In absolute 2025 MAE the pick was worse in most cells:

| cell | shipped | picked |
| --- | ---: | ---: |
| pass_yds QB | 66.769 | 66.847 |
| rec_yds WR | 25.488 | 25.676 |
| rec WR | 1.554 | 1.567 |
| rush_yds QB | 26.179 | 26.589 |
| rush_car QB | 3.553 | 3.612 |
| pass_td QB | 0.932 | 0.930 |
| rush_car RB | 4.081 | 4.073 |

The shipped settings (3.0 / 0.25 / 0.88) stay. The script now pins the naive
baseline's half-life so a future run ranks candidates on a fixed yardstick.

# Fair-Line & Arbitrage Detection Engine

Quantitative measurement of sports betting market efficiency: multi-book odds
ingestion, vig removal, learned book-sharpness consensus (forecast
combination), cross-book arbitrage window measurement, and a bias-controlled
backtest whose FIRST reported metric is execution-lag sensitivity.

**Design doc:** `devig-arb-project-plan.md` (tickets T1-T13, pitfalls P1-P9).

## Status
- [x] T1 scaffold - T3 devig - T4 signal engine/arb - staking - sharpness -
  backtest engine: built, 33 tests green (`pytest -q`)
- [ ] T2/T5 live polling (needs ODDS_API_KEY + repo secrets)
- [ ] T7 deploy - T8/T11 on real data (needs ~100 games) - T12 webhook

## Honesty machinery (why this differs from a signals screen)
Execution-lag sweep {0,1,3,5,10 min} as the headline table; signal-staleness
exclusion; closing-line quality tiers; EV-bucket calibration on a 60/40
chronological split; walk-forward sharpness weights (look-ahead-invariance
unit-tested); expected-vs-realized tracking.

## Staking gates (hard)
No real money before: positive lag-adjusted CLV in backtest, then >=150
forward paper-traded signals with positive lag-adjusted CLV. Bankroll floor
~$3-5k below which staking is research, not income. Offshore exposure <=30%.

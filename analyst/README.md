# `analyst/` — Phase 2 placeholder

This package is reserved for the **recommendation layer** of Djohodo. It is
intentionally empty in Phase 1.

## Role

Where `watcher/` **reports** what is happening (24h news digest), `analyst/`
will **help decide**: it will read the watcher's output (and, eventually,
portfolio sizing, conviction levels, and risk constraints) and surface
`buy` / `sell` / `watch` signals with a short rationale.

## Planned extension point

The current Phase 1 pipeline is:

```
portfolio.json ──► watcher.run_watch ──► WatchResult(digest, model, total_cost_usd)
                                              │
                                              ▼
                                      watcher.delivery.deliver
```

Phase 2 will insert one new step:

```
WatchResult ──► analyst.analyze(watch_result, portfolio) ──► AnalysisResult
                                                                 │
                                                                 ▼
                                                          delivery (extended)
```

Concretely, when Phase 2 starts, add:

- `analyst/agent.py` — `async def analyze(watch_result, portfolio) -> AnalysisResult`
- `analyst/prompt.py` — the recommendation prompt template
- An `AnalysisResult` dataclass with `signals: list[Signal]` where
  `Signal = {ticker, action, conviction, rationale}`.

`main.py` already centralises the orchestration, so wiring the analyst in
will be a localised change — no need to rework the watcher.

## Non-goals (for now)

- No execution / broker integration.
- No position sizing.
- No backtesting.

The analyst remains an **informational decision-support** layer, consistent
with the project's "not financial advice" stance.

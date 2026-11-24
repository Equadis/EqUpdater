# Testing

```
python tools/check.py                       # everything (929 tests, ~2.5 min)
python -m unittest tests.test_v3_core tests.test_v3_skin tests.test_v3_qt
```

| File | Covers |
|---|---|
| `tests/__init__.py` | Gives every run a throwaway `HOME`/`USERPROFILE` so no test can touch a real `~/Games/OctoWoW` (this was the cause of the four pre-existing failures; see BASELINE.md) |
| `test_v3_core.py` | TaskManager (dedupe, bounded, cancel), JsonStore (debounce, corrupt file, migration), NewsService (cache-first, one fetch per topic, incremental load, in-place update, closed posts stay closed, stale body refresh, pinned-topic rule, NEW marks), link HTML (escaping, `javascript:` dropped, overlaps) |
| `test_v3_skin.py` | every component id resolves; inheritance and clearing; nine-slice; missing asset, `..`, absolute/drive paths, symlink out, wrong extension/magic/size, oversize JSON, bad manifest/future `skin_api`, cyclic `extends`, palette refs; both shipped skins load with **zero** problems |
| `test_v3_qt.py` | the real QML scene (offscreen, software renderer): no QML warnings; cached news before any request; scrolling asserts **no request** and exactly **one scrolling item per panel**; cached expand makes no request; Load more appends without a model reset; scroll anchor stable when a post above changes; offline with and without cache; live skin switching on every page with no errors; broken skin falls back; restore default; animation gating (minimise/unfocus/setting); launch options, saved choice, missing file keeps choice, Play with a fake spawner; 2.x import never writes |
| existing 2.x suite | unchanged and green (one platform test now patches `core/net.py`, which holds the moved TLS code) |

Not covered yet: pixel-level visual regression (screenshots are taken by `EQU3_SCREENSHOT` and reviewed by eye, `bench-results/shots/`), long soak/leak runs, Linux CI for the Qt tests, every skin × every page *visually* (only loading without errors is asserted for the pages that exist), HiDPI.

`bench-results/` and `docs/v3/PERFORMANCE.md` hold benchmark data; the harness is in `tools/bench/`.

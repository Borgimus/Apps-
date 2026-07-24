# Verify Skill — Options Trading Research System

## Surfaces

| What changed | Surface | Command |
|---|---|---|
| Scanner auth / data fetch | `scripts/validate_scanner.py` | `python scripts/validate_scanner.py --symbols 3` |
| Intraday bars / signal gen | `scripts/readiness_check.py` | `python scripts/readiness_check.py --symbols RIVN` |
| Broker connectivity | `scripts/test_alpaca.py` | `python scripts/test_alpaca.py` |
| Full session pipeline | `scripts/session_runner.py` | Runs 3-hour paper session; use only for S-series sessions |

## Auth pattern to verify

Both Alpaca clients (`yfinance_scanner.py::_make_alpaca_client()` and
`yfinance_data.py::_make_alpaca_data_client()`) fall back to pydantic settings
when OS env vars are absent. To confirm the fallback fires:

```
echo "OS env: $([ -n "$ALPACA_API_KEY" ] && echo HAS_KEY || echo EMPTY)"
# Should print EMPTY — creds live in .env/config, not exported to shell
python -c "from app.config import get_settings; s=get_settings(); print(s.alpaca_api_key[:6])"
```

## Gotchas

- `ALPACA_API_KEY` is NOT in OS env; must come from pydantic settings (`.env` / `config.yaml`).
- SSL: proxy CA at `$REQUESTS_CA_BUNDLE` must be set; `_make_alpaca_client()` uses it.
- Stage 2 of `validate_scanner.py` may hit transient TLS timeouts on heavy batch-fetch
  (75-day daily bars for 3+ symbols). If Stage 2 fails but direct API calls succeed,
  it's transient — retry or test with `readiness_check.py` instead.
- Market closed / pre-market: scanner candidates all reject `low_volume_chop` (rvol<0.5).
  Normal. Run readiness check with a specific symbol to bypass volume scoring.
- `validate_scanner.py --no-alpaca` skips Stage 4 (Alpaca confirmation) but Stage 7
  still runs `get_intraday_bars()` if any candidates pass scoring.

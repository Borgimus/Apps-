# Database Schema

PostgreSQL in deployment; SQLite for isolated local dev/tests. All timestamps are UTC (`timestamptz`),
with the market/session date stored separately as `date` (America/New_York). The schema is designed so
that **every decision can be reconstructed** from stored inputs, config, market data, and rule outputs.

Migrations are versioned (Alembic). Runtime DBs and raw account/candidate files are **never** committed.

## Tables

### tc2000_imports
| column | type | notes |
|--------|------|-------|
| id | uuid pk | |
| market_date | date | America/New_York |
| received_at | timestamptz | |
| batch_hash | text | SHA-256 over the three raw files (sorted) |
| status | text | `ACCEPTED` / `REJECTED` |
| reject_reason | text null | |
| config_version | text | strategy config in force |

### tc2000_scan_files
| id | uuid pk |
| import_id | uuid fk → tc2000_imports |
| scan | text | `one_month`/`three_month`/`six_month` |
| filename | text |
| file_hash | text | SHA-256 |
| raw_path | text | preserved raw file location |
| symbol_count | int |

### candidate_membership
| id | uuid pk |
| import_id | uuid fk |
| symbol | text |
| in_1m / in_3m / in_6m | bool |
| agreement_count | int | 0–3 |
| mode_3of3 / mode_2of3 / mode_union | bool | which sets it belongs to |
| composite_strength | numeric null | union ranking score |
| source | text | `TC2000` |

### market_snapshots
| id | uuid pk |
| symbol | text |
| as_of | timestamptz |
| feed | text | `iex`/`sip` |
| bar_timestamp | timestamptz | latest bar used |
| adjusted | bool |
| ohlcv_json | jsonb | the bar window used for the decision |
| staleness_seconds | numeric |

### indicator_values
| id | uuid pk | symbol | as_of | timeframe (`daily`/`hourly`) |
| sma10/sma20/sma50/sma200 | numeric |
| vol_ema22 | numeric |
| adr_pct / atr_pct | numeric |
| dollar_volume | numeric |
| slope10/slope20/slope50/slope200 | numeric | normalized slope % |
| snapshot_id | uuid fk → market_snapshots |

### setups
| id | uuid pk | candidate symbol | setup_version | created_at |
| contraction_start / contraction_end | date |
| breakout_level | numeric | highest validated range high |
| components_json | jsonb | each scored/required component result |
| setup_score | numeric |
| required_pass | bool | all required components passed |
| config_version | text |

### risk_calcs
| id | uuid pk | signal_id fk |
| risk_equity | numeric | reconciled Alpaca paper equity used |
| risk_fraction | numeric |
| expected_entry / initial_stop | numeric |
| risk_per_share / risk_dollars | numeric |
| raw_shares / capped_shares | int |
| caps_json | jsonb | buying-power/allocation/liquidity/notional caps applied |

### signals
| id | uuid pk | symbol | setup_id fk | created_at |
| kind | text | `ENTRY`/`PARTIAL`/`FINAL_EXIT` |
| state | text | strategy state at emission |
| accepted | bool | reject_reason text null |
| idempotency_key | text unique |

### orders
| id | uuid pk | signal_id fk | client_order_id | text unique (idempotent) |
| broker_order_id | text null | side | type (`stop_limit`/`limit`/`stop`) |
| limit_price / stop_price | numeric |
| qty | int | status | text |
| submitted_at / acknowledged_at | timestamptz |

### fills
| id | uuid pk | order_id fk | fill_qty | int | fill_price | numeric | filled_at | timestamptz |
| cumulative_qty | int | vwap_price | numeric |

### order_replacements
| id | uuid pk | order_id fk | reason | old_stop/new_stop | numeric | at | timestamptz |

### position_state_transitions
| id | uuid pk | trade_id | from_state | to_state |
| guard_results_json | jsonb | idempotency_key | text unique |
| reason | text | at | timestamptz |

### daily_account_snapshots
| id | uuid pk | session_date | date |
| equity / cash / buying_power | numeric |
| committed_risk / exposure | numeric |
| realized_pnl / unrealized_pnl | numeric | drawdown | numeric |
| endpoint | text | must equal the verified paper endpoint |

### pnl
| id | uuid pk | trade_id | realized | numeric | unrealized | numeric | r_multiple | numeric | as_of |

### ai_reviews
| id | uuid pk | subject_type | subject_id |
| model_name | text | prompt_version | text |
| prompt / output | text |
| tokens_in/tokens_out/cost_usd | numeric |
| at | timestamptz | **advisory only — never authoritative** |

### reconciliation_incidents
| id | uuid pk | detected_at |
| kind | text | `POSITION_MISMATCH`/`ORDER_MISMATCH`/`MISSING_STOP`/`STALE_DATA` |
| broker_state_json / db_state_json | jsonb |
| resolved | bool | resolution_note text |

## Reconstruction guarantee

Given `config_version`, the raw TC2000 files (`tc2000_scan_files.raw_path`), and
`market_snapshots.ohlcv_json`, the deterministic scanners/risk/state-machine reproduce
`indicator_values`, `setups`, `risk_calcs`, `signals`, and `position_state_transitions` byte-for-byte.
AI rows are advisory and excluded from the authoritative decision record.

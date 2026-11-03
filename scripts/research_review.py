#!/usr/bin/env python3
"""Rebuild cumulative research from local evidence. No broker/network access."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.research.portfolio_review import build_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=Path(os.getenv('TRADER_EVIDENCE_DIR', '/root/trader-evidence')))
    args = parser.parse_args()
    report = build_review(ROOT, args.evidence)
    print('RESEARCH_REVIEW_COMPLETE ' + json.dumps({key: report[key] for key in (
        'as_of_session', 'full_valid_sessions', 'partial_sessions', 'invalid_sessions', 'stage')}))
    return 1 if report['invalid_sessions'] else 0


if __name__ == '__main__':
    raise SystemExit(main())

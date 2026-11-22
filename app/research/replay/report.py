"""Deterministic research reports. Null fields stay visible."""
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(text)
        tmp = Path(temp.name)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def json_write(path, result):
    atomic_text(path, json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")


def markdown(result):
    primary = result.get("primary") or {}
    lines = [f"# {result['id']} | {result['status']}", "", result.get("reason", ""), "",
             "Price or volatility proxies. Orders disabled.", "",
             "| Statistic | Value |", "| --- | --- |"]
    for key, val in (("Sample size", primary.get("n")), ("Independent candidate days or months", primary.get("candidate_days")),
                     ("Incremental mean (bps)", primary.get("mean_bps")), ("t-statistic", primary.get("t"))):
        lines.append(f"| {key} | {val if val is not None else 'Unavailable'} |")
    for key, val in result.get("checks", {}).items():
        lines.append(f"| {key} | {'Pass' if val else 'Fail'} |")
    return "\n".join(lines).rstrip() + "\n"


def save_report(directory, name, result):
    json_write(directory / f"{name}.json", result)
    atomic_text(directory / f"{name}.md", markdown(result))

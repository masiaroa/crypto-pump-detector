from pathlib import Path

from pump_detector.config import ROOT
from pump_detector.scanner import scan_to_csv


if __name__ == "__main__":
    df = scan_to_csv(Path(ROOT / "data" / "latest_scan.csv"), persist=True)
    print(df[["symbol", "timeframe", "close", "oi_change_pct", "funding_classification", "early_bullish_score", "blowoff_risk_score", "signal_active", "notes"]].to_string(index=False))

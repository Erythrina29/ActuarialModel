# ActuarialModel

import sys
from pathlib import Path

PROJECT_ROOT = Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from waiver_reserve.config import load_monthly_config

config_last = load_monthly_config("2026-05/config/waiver_assumptions.yaml")
config_this = load_monthly_config("2026-06/config/waiver_assumptions.yaml")

print("LAST valuation_date:", config_last["valuation_date"])
print("THIS valuation_date:", config_this["valuation_date"])

print("LAST interest_rate:", config_last.get("interest_rate"))
print("THIS interest_rate:", config_this.get("interest_rate"))

print("LAST FX:", config_last.get("usd_to_idr_rate"))
print("THIS FX:", config_this.get("usd_to_idr_rate"))
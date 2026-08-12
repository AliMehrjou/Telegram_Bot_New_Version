"""
scripts/seed_vip_plans.py

v3 NEW: Validate that vip_plans.json is correctly formatted.
(Plans are not stored in DB — they're read directly from JSON.)
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # FIX: was one level short -- needs /app, not /app/matching_bot_project, on sys.path for "import matching_bot_project.xxx" to resolve


async def validate_vip_plans():
    json_path = Path("json_files/vip_plans.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/vip_plans.json")
    if not json_path.exists():
        print("ERROR: vip_plans.json not found")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required_codes = ["1w", "2w", "1m"]
    for code in required_codes:
        if code not in data:
            print(f"ERROR: missing plan '{code}'")
            return
        plan = data[code]
        if "label" not in plan or "duration_days" not in plan or "price_toman" not in plan:
            print(f"ERROR: plan '{code}' missing required fields")
            return

    print("✅ VIP plans validated:")
    for code, plan in data.items():
        print(f"  • {plan['label']} — {plan['duration_days']} days — {plan['price_toman']:,} Toman")


if __name__ == "__main__":
    asyncio.run(validate_vip_plans())

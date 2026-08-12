"""
scripts/seed_gifts.py

v3 NEW: Seed the gift_types table from json_files/gifts_catalog.json.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # FIX: was one level short -- needs /app, not /app/matching_bot_project, on sys.path for "import matching_bot_project.xxx" to resolve

from matching_bot_project.database.session import async_session_factory
from matching_bot_project.database.models.models import GiftType
from sqlalchemy import select


async def seed_gifts():
    json_path = Path("json_files/gifts_catalog.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/gifts_catalog.json")
    if not json_path.exists():
        print("ERROR: gifts_catalog.json not found")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    gifts = data.get("gifts", [])
    if not gifts:
        print("ERROR: no gifts found in JSON")
        return

    async with async_session_factory() as session:
        added = 0
        updated = 0
        for gift_data in gifts:
            result = await session.execute(
                select(GiftType).where(GiftType.code == gift_data["code"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.display_name = gift_data["display_name"]
                existing.emoji = gift_data["emoji"]
                existing.price_coins = gift_data["price_coins"]
                existing.description = gift_data.get("description")
                existing.is_active = gift_data.get("is_active", True)
                existing.sort_order = gift_data.get("sort_order", 0)
                updated += 1
            else:
                gift = GiftType(
                    code=gift_data["code"],
                    display_name=gift_data["display_name"],
                    emoji=gift_data["emoji"],
                    price_coins=gift_data["price_coins"],
                    description=gift_data.get("description"),
                    is_active=gift_data.get("is_active", True),
                    sort_order=gift_data.get("sort_order", 0),
                )
                session.add(gift)
                added += 1

        await session.commit()
        print(f"✅ Seeded gifts: {added} added, {updated} updated")


if __name__ == "__main__":
    asyncio.run(seed_gifts())

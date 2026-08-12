"""
scripts/seed_tags.py

v3 NEW: Seed the tag_catalog table from json_files/tags_catalog.json.
Run this once after applying migrations:

    python scripts/seed_tags.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # FIX: was one level short -- needs /app, not /app/matching_bot_project, on sys.path for "import matching_bot_project.xxx" to resolve

from matching_bot_project.database.session import async_session_factory
from matching_bot_project.database.models.models import TagCatalog
from sqlalchemy import select


async def seed_tags():
    json_path = Path("json_files/tags_catalog.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/tags_catalog.json")
    if not json_path.exists():
        print(f"ERROR: tags_catalog.json not found")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tags = data.get("tags", [])
    if not tags:
        print("ERROR: no tags found in JSON")
        return

    async with async_session_factory() as session:
        added = 0
        updated = 0
        for tag_data in tags:
            result = await session.execute(
                select(TagCatalog).where(TagCatalog.code == tag_data["code"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Update
                existing.display_name = tag_data["display_name"]
                existing.emoji = tag_data.get("emoji")
                existing.category = tag_data.get("category", "lifestyle")
                existing.is_active = True
                existing.sort_order = tag_data.get("sort_order", 0)
                updated += 1
            else:
                # Insert
                tag = TagCatalog(
                    code=tag_data["code"],
                    display_name=tag_data["display_name"],
                    emoji=tag_data.get("emoji"),
                    category=tag_data.get("category", "lifestyle"),
                    is_active=True,
                    sort_order=tag_data.get("sort_order", 0),
                )
                session.add(tag)
                added += 1

        await session.commit()
        print(f"✅ Seeded tags: {added} added, {updated} updated")


if __name__ == "__main__":
    asyncio.run(seed_tags())

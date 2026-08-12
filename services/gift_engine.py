"""
services/gift_engine.py

v3 NEW: Gift engine — purchase, transfer, and inventory management.

Gifts are purchasable items (teddy, rose, diamond, etc.) that:
- Cost coins to buy (price defined per gift type)
- Can be transferred between users (free — no coin cost)
- Are displayed on the receiver's profile (gift count per type)
- Serve as a social/status feature (similar to Telegram gift stickers)

Tables involved:
- gift_types        — catalog of available gifts
- user_gifts         — inventory (one row per user × gift_type)
- gift_transactions  — log of purchases and transfers
- coin_transactions  — coin deduction record (for purchases)
"""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update, and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import (
    User, GiftType, UserGift, GiftTransaction, CoinTransaction,
)
from matching_bot_project.bot.core.constants import GiftCode

logger = logging.getLogger(__name__)


# Cache gift catalog loaded from JSON file
_GIFT_CATALOG_CACHE: Optional[dict] = None
_GIFT_CATALOG_LOADED_AT: Optional[datetime] = None

def load_gift_catalog() -> dict:
    """Load gift catalog from json_files/gifts_catalog.json (cached for 5 min)."""
    global _GIFT_CATALOG_CACHE, _GIFT_CATALOG_LOADED_AT
    now = datetime.now(timezone.utc)
    
    # استفاده از کش اگر کمتر از ۵ دقیقه از لود قبلی گذشته باشد
    if _GIFT_CATALOG_CACHE and _GIFT_CATALOG_LOADED_AT and (now - _GIFT_CATALOG_LOADED_AT).total_seconds() < 300:
        return _GIFT_CATALOG_CACHE

    # بررسی مسیرهای احتمالی فایل JSON
    json_path = Path("json_files/gifts_catalog.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/gifts_catalog.json")
        
    if not json_path.exists():
        # حالت فال‌بک: تولید کاتالوگ از مقادیر پیش‌فرض و ثابت در کد به همراه پشتیبانی از فیلد description
        _GIFT_CATALOG_CACHE = {
            code: {
                "code": code,
                "display_name": GiftCode.LABELS.get(code, code),
                "emoji": GiftCode.EMOJIS.get(code, ""),
                "price_coins": GiftCode.DEFAULT_PRICES_COINS.get(code, 0),
                "description": None  # اضافه شدن فیلد توضیحات در ساختار پیش‌فرض
            }
            for code in GiftCode.ALL
        }
    else:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # استخراج دیتا از JSON: چون کل دیکشنری item ذخیره می‌شود، 
            # فیلد description هم مستقیماً از فایل خوانده شده و در کش قرار می‌گیرد
            _GIFT_CATALOG_CACHE = {item["code"]: item for item in data.get("gifts", [])}
        except Exception as e:
            logger.error("Error loading gifts_catalog.json: %s", e)
            _GIFT_CATALOG_CACHE = {}
            
    _GIFT_CATALOG_LOADED_AT = now
    return _GIFT_CATALOG_CACHE


class GiftEngine:
    """Manages gift purchases, transfers, and inventory."""

    def __init__(self, redis_client=None):
        self.redis = redis_client

    async def sell_gift(
        self,
        session: AsyncSession,
        user_tg_id: int,
        gift_code: str,
        quantity: int = 1,
    ) -> tuple[bool, str]:
        """
        Sell a gift back to the system for 80% of its original coin price.
        """
        if quantity < 1:
            return False, "تعداد نامعتبر است."

        gift_type = await self.get_gift_type_by_code(session, gift_code)
        if not gift_type:
            return False, "گیفت یافت نشد."

        # Lock user's inventory row
        result = await session.execute(
            select(UserGift).where(
                and_(
                    UserGift.owner_tg_id == user_tg_id,
                    UserGift.gift_type_id == gift_type.id,
                )
            ).with_for_update()
        )
        user_inv = result.scalar_one_or_none()
        
        if not user_inv or user_inv.quantity < quantity:
            return False, f"شما به اندازه کافی {gift_type.display_name} برای فروش ندارید."

        # Lock user row to update coin balance
        user_result = await session.execute(
            select(User).where(User.tg_id == user_tg_id).with_for_update()
        )
        user = user_result.scalar_one_or_none()
        if not user:
            return False, "کاربر یافت نشد."

        # محاسبه مبلغ بازگشتی (۸۰ درصد قیمت اصلی)
        sell_price_per_unit = (gift_type.price_coins * 80) // 100
        total_earned = sell_price_per_unit * quantity

        # کسر از موجودی گیفت کاربر
        user_inv.quantity -= quantity
        user_inv.last_source = "sell"

        # واریز سکه به حساب کاربر
        user.coin_balance += total_earned

        # ثبت تراکنش گیفت
        gt = GiftTransaction(
            sender_tg_id=user_tg_id,
            receiver_tg_id=user_tg_id, # گیرنده‌ای وجود ندارد، سیستم خریدار است
            gift_type_id=gift_type.id,
            quantity=quantity,
            tx_kind="sell",
            coins_spent=-total_earned, # عدد منفی برای نشان دادن درآمد کاربر
        )
        session.add(gt)
        await session.flush()

        # ثبت تراکنش سکه
        ct = CoinTransaction(
            user_id=user_tg_id,
            amount=total_earned,
            description=f"فروش {quantity}× {gift_type.display_name}",
            reference_id=gt.id,
            tx_type="gift_sell",
        )
        session.add(ct)

        await session.commit()
        return True, f"✅ {quantity} عدد {gift_type.display_name} فروخته شد و {total_earned} سکه به حساب شما واریز گردید."
    
    async def get_gift_type_by_code(
        self, session: AsyncSession, code: str
    ) -> Optional[GiftType]:
        result = await session.execute(
            select(GiftType).where(GiftType.code == code)
        )
        return result.scalar_one_or_none()

    async def get_all_active_gift_types(self, session: AsyncSession) -> list:
        result = await session.execute(
            select(GiftType)
            .where(GiftType.is_active == True)
            .order_by(GiftType.sort_order, GiftType.id)
        )
        return result.scalars().all()

    async def get_user_inventory(self, session: AsyncSession, user_tg_id: int) -> list:
        """Return list of (gift_type, quantity) for a user."""
        result = await session.execute(
            select(UserGift, GiftType)
            .join(GiftType, UserGift.gift_type_id == GiftType.id)
            .where(UserGift.owner_tg_id == user_tg_id)
            .order_by(GiftType.sort_order)
        )
        return [(ug, gt) for ug, gt in result.all()]

    async def get_user_gifts_summary(self, session: AsyncSession, user_tg_id: int) -> dict:
        """Return {emoji: quantity} map for profile display."""
        inventory = await self.get_user_inventory(session, user_tg_id)
        return {gt.emoji: ug.quantity for ug, gt in inventory if ug.quantity > 0}

    async def purchase_gift(
        self,
        session: AsyncSession,
        buyer_tg_id: int,
        gift_code: str,
        quantity: int = 1,
    ) -> tuple[bool, str]:
        """
        Buy a gift (deducts coins from buyer).
        Returns (success, message).

        FIX PHASE1-CRIT-03: when this method was called from the Zarinpal
        callback (api/routes/payment.py) for an order_type=="gift_purchase",
        the user had ALREADY paid Toman via the gateway. Calling this method
        again deducted coins from `coin_balance` → double-charge. Use the new
        `credit_gift()` method for gateway-paid purchases.
        """
        if quantity < 1 or quantity > 100:
            return False, "تعداد نامعتبر است."

        # Lock user row for atomic balance check
        result = await session.execute(
            select(User).where(User.tg_id == buyer_tg_id).with_for_update()
        )
        buyer = result.scalar_one_or_none()
        if not buyer:
            return False, "کاربر یافت نشد."

        gift_type = await self.get_gift_type_by_code(session, gift_code)
        if not gift_type or not gift_type.is_active:
            return False, "این گیفت در حال حاضر موجود نیست."

        total_cost = gift_type.price_coins * quantity
        if buyer.coin_balance < total_cost:
            return False, f"موجودی سکه کافی نیست. {total_cost} سکه لازم دارید."

        # Deduct coins
        buyer.coin_balance -= total_cost
        buyer.total_spent_coins += total_cost

        # Add gift to buyer's inventory
        existing = await session.execute(
            select(UserGift).where(
                and_(
                    UserGift.owner_tg_id == buyer_tg_id,
                    UserGift.gift_type_id == gift_type.id,
                )
            )
        )
        user_gift = existing.scalar_one_or_none()
        if user_gift:
            user_gift.quantity += quantity
            user_gift.last_source = "purchase"
        else:
            user_gift = UserGift(
                owner_tg_id=buyer_tg_id,
                gift_type_id=gift_type.id,
                quantity=quantity,
                last_source="purchase",
            )
            session.add(user_gift)

        # Record gift transaction
        gt = GiftTransaction(
            sender_tg_id=buyer_tg_id,
            receiver_tg_id=buyer_tg_id,
            gift_type_id=gift_type.id,
            quantity=quantity,
            tx_kind="purchase",
            coins_spent=total_cost,
        )
        session.add(gt)
        # FIX HIGH (gift_engine): flush so gt.id is populated before CoinTransaction
        # references it — otherwise reference_id is always None and the audit trail
        # is broken.
        await session.flush()

        # Record coin transaction
        ct = CoinTransaction(
            user_id=buyer_tg_id,
            amount=-total_cost,
            description=f"خرید {quantity}× {gift_type.display_name}",
            reference_id=gt.id,
            tx_type="gift_purchase",
        )
        session.add(ct)

        await session.commit()
        return True, f"🎉 {quantity}× {gift_type.emoji} {gift_type.display_name} به کیف شما اضافه شد!"

    async def credit_gift(
        self,
        session: AsyncSession,
        buyer_tg_id: int,
        gift_code: str,
        quantity: int = 1,
        payment_order_id: Optional[int] = None,
    ) -> tuple[bool, str]:
        """
        Credit a gift to a user's inventory WITHOUT deducting coins.
        Use this for gateway-paid purchases (Zarinpal Toman path) — the user
        has already paid real money, so we must NOT also deduct coins.

        FIX PHASE1-CRIT-03: previous code called purchase_gift() from the
        Zarinpal callback, double-charging the user (Toman + coins).
        """
        if quantity < 1 or quantity > 100:
            return False, "تعداد نامعتبر است."

        gift_type = await self.get_gift_type_by_code(session, gift_code)
        if not gift_type or not gift_type.is_active:
            return False, "این گیفت در حال حاضر موجود نیست."

        # Add gift to buyer's inventory (no coin deduction)
        existing = await session.execute(
            select(UserGift).where(
                and_(
                    UserGift.owner_tg_id == buyer_tg_id,
                    UserGift.gift_type_id == gift_type.id,
                )
            )
        )
        user_gift = existing.scalar_one_or_none()
        if user_gift:
            user_gift.quantity += quantity
            user_gift.last_source = "gateway_purchase"
        else:
            user_gift = UserGift(
                owner_tg_id=buyer_tg_id,
                gift_type_id=gift_type.id,
                quantity=quantity,
                last_source="gateway_purchase",
            )
            session.add(user_gift)

        # Record gift transaction (no coins spent — paid via gateway)
        gt = GiftTransaction(
            sender_tg_id=buyer_tg_id,
            receiver_tg_id=buyer_tg_id,
            gift_type_id=gift_type.id,
            quantity=quantity,
            tx_kind="gateway_purchase",
            coins_spent=0,
        )
        session.add(gt)
        await session.flush()

        # Optional: record a coin transaction with amount=0 so the audit trail
        # links the gift_transaction to the payment_order. This makes finance
        # reconciliation possible without affecting coin_balance.
        if payment_order_id is not None:
            ct = CoinTransaction(
                user_id=buyer_tg_id,
                amount=0,
                description=f"خرید تومانی {quantity}× {gift_type.display_name} (سفارش #{payment_order_id})",
                reference_id=gt.id,
                tx_type="gift_gateway_purchase",
            )
            session.add(ct)

        await session.commit()
        return True, f"🎉 {quantity}× {gift_type.emoji} {gift_type.display_name} به کیف شما اضافه شد!"

    async def transfer_gift(
        self,
        session: AsyncSession,
        sender_tg_id: int,
        receiver_tg_id: int,
        gift_code: str,
        quantity: int = 1,
    ) -> tuple[bool, str]:
        """
        Transfer a gift from sender to receiver (free — no coin cost).
        Returns (success, message).
        """
        if sender_tg_id == receiver_tg_id:
            return False, "نمی‌توانید به خودتان گیفت بفرستید."
        if quantity < 1:
            return False, "تعداد نامعتبر است."

        gift_type = await self.get_gift_type_by_code(session, gift_code)
        if not gift_type:
            return False, "گیفت یافت نشد."

        # Lock sender's inventory row
        result = await session.execute(
            select(UserGift).where(
                and_(
                    UserGift.owner_tg_id == sender_tg_id,
                    UserGift.gift_type_id == gift_type.id,
                )
            ).with_for_update()
        )
        sender_inv = result.scalar_one_or_none()
        if not sender_inv or sender_inv.quantity < quantity:
            return False, f"شما به اندازه کافی {gift_type.display_name} ندارید."

        # Deduct from sender
        sender_inv.quantity -= quantity
        sender_inv.last_source = "transfer_out"

        # Add to receiver
        result = await session.execute(
            select(UserGift).where(
                and_(
                    UserGift.owner_tg_id == receiver_tg_id,
                    UserGift.gift_type_id == gift_type.id,
                )
            )
        )
        receiver_inv = result.scalar_one_or_none()
        if receiver_inv:
            receiver_inv.quantity += quantity
            receiver_inv.last_source = "transfer_in"
        else:
            receiver_inv = UserGift(
                owner_tg_id=receiver_tg_id,
                gift_type_id=gift_type.id,
                quantity=quantity,
                last_source="transfer_in",
            )
            session.add(receiver_inv)

        # Record gift transaction
        gt = GiftTransaction(
            sender_tg_id=sender_tg_id,
            receiver_tg_id=receiver_tg_id,
            gift_type_id=gift_type.id,
            quantity=quantity,
            tx_kind="transfer",
            coins_spent=0,
        )
        session.add(gt)

        await session.commit()
        return True, f"🎁 {quantity}× {gift_type.emoji} {gift_type.display_name} ارسال شد!"

    async def get_recent_gift_transactions(
        self, session: AsyncSession, user_tg_id: int, limit: int = 10
    ) -> list:
        """Return recent gift transactions (sent or received) for a user."""
        result = await session.execute(
            select(GiftTransaction, GiftType)
            .join(GiftType, GiftTransaction.gift_type_id == GiftType.id)
            .where(
                or_(
                    GiftTransaction.sender_tg_id == user_tg_id,
                    GiftTransaction.receiver_tg_id == user_tg_id,
                )
            )
            .order_by(GiftTransaction.created_at.desc())
            .limit(limit)
        )
        return result.all()

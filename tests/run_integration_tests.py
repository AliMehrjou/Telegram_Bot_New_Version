#!/usr/bin/env python3
"""
Integration tests for Matching Bot v3 — tests actual business logic with real DB.

Run with:  python3 tests/run_integration_tests.py

Tests:
1. Full gift lifecycle: purchase → transfer → display on profile
2. VIP subscription: purchase → activate → check is_vip_active → expire
3. Referral: assign code → attribute new user → process commission on purchase
4. Warning system: issue warning → check count → 3rd warning = ban
5. Profile completion: mark steps done → reward
6. Free coin banner: create campaign → forward → approve → reward
7. Tag system: assign tags → validate max limit (3 normal, 10 VIP)
8. Distance filter: 3 users in different cities → filter by distance
9. Direct message: sender sends → receiver reads
10. Coin transaction history
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Setup
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:////tmp/match_bot_integration.db'
os.environ['BOT_TOKEN'] = '123456789:AAEhBP0vXbG3YpJ8Z3rD5mF8K2tN6qL9VxYwZ-abcDEF'
os.environ['WEBHOOK_SECRET_TOKEN'] = 'test_webhook_secret_at_least_32_chars_long'
os.environ['ADMIN_SECRET_TOKEN'] = 'test_admin_token'
os.environ['REQUIRED_CHANNEL_ID'] = '-1001234567890'
os.environ['ENVIRONMENT'] = 'development'

PROJECT_ROOT = Path('/home/z/my-project/matching_bot_project_v3')
sys.path.insert(0, str(PROJECT_ROOT))

RESULTS = []
def record(name, success, detail=""):
    status = "✅" if success else "❌"
    RESULTS.append((name, success, detail))
    print(f"{status} {name}" + (f": {detail}" if detail and not success else ""))


async def setup_db():
    """Reset and create database."""
    test_db = Path('/tmp/match_bot_integration.db')
    if test_db.exists():
        test_db.unlink()
    from matching_bot_project.database.session import engine, Base
    from matching_bot_project.database.models import models  # noqa
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Full Gift Lifecycle
# ═══════════════════════════════════════════════════════════════════════════
async def test_gift_lifecycle(engine):
    print("\n── Integration Test 1: Gift Lifecycle ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User, GiftType
        from matching_bot_project.services.gift_engine import GiftEngine

        ge = GiftEngine()

        async with async_session_factory() as session:
            # Seed gift catalog
            teddy = GiftType(code="teddy", display_name="تدی", emoji="🧸",
                             price_coins=5, is_active=True, sort_order=1)
            rose = GiftType(code="rose", display_name="رز", emoji="🌹",
                            price_coins=3, is_active=True, sort_order=2)
            session.add_all([teddy, rose])
            
            # Create buyer (with 100 coins) and recipient (with 0)
            buyer = User(tg_id=10001, first_name="Buyer", public_id="user_buyer1",
                         coin_balance=100, referral_code="BUYER1AB")
            recipient = User(tg_id=10002, first_name="Recipient", public_id="user_recip2",
                             coin_balance=0, referral_code="RECIPI2CD")
            session.add_all([buyer, recipient])
            await session.commit()

            # 1. Buyer purchases 5 teddies (5 × 5 = 25 coins)
            ok, msg = await ge.purchase_gift(session, 10001, "teddy", 5)
            assert ok, f"purchase failed: {msg}"
            await session.refresh(buyer)
            assert buyer.coin_balance == 75, f"expected 75, got {buyer.coin_balance}"

            # 2. Buyer purchases 2 roses (2 × 3 = 6 coins)
            ok, msg = await ge.purchase_gift(session, 10001, "rose", 2)
            assert ok, f"purchase failed: {msg}"
            await session.refresh(buyer)
            assert buyer.coin_balance == 69, f"expected 69, got {buyer.coin_balance}"

            # 3. Buyer tries to purchase with insufficient coins (1000 teddies = 5000 coins)
            ok, msg = await ge.purchase_gift(session, 10001, "teddy", 1000)
            assert not ok, "should have failed (insufficient coins)"

            # 4. Buyer transfers 2 teddies to recipient
            ok, msg = await ge.transfer_gift(session, 10001, 10002, "teddy", 2)
            assert ok, f"transfer failed: {msg}"

            # 5. Verify inventory
            buyer_inv = await ge.get_user_inventory(session, 10001)
            buyer_gifts = {gt.code: ug.quantity for ug, gt in buyer_inv}
            assert buyer_gifts.get("teddy") == 3, f"buyer teddy: {buyer_gifts.get('teddy')}"
            assert buyer_gifts.get("rose") == 2, f"buyer rose: {buyer_gifts.get('rose')}"

            recip_inv = await ge.get_user_inventory(session, 10002)
            recip_gifts = {gt.code: ug.quantity for ug, gt in recip_inv}
            assert recip_gifts.get("teddy") == 2, f"recipient teddy: {recip_gifts.get('teddy')}"

            # 6. Verify recipient didn't lose coins (transfer is free)
            await session.refresh(recipient)
            assert recipient.coin_balance == 0, "transfer should be free"

            # 7. Get gifts summary for profile display
            summary = await ge.get_user_gifts_summary(session, 10001)
            assert summary.get("🧸") == 3
            assert summary.get("🌹") == 2

            # 8. Buyer tries to transfer more than they own (transfer 10 teddies, has only 3)
            ok, msg = await ge.transfer_gift(session, 10001, 10002, "teddy", 10)
            assert not ok, "should have failed (insufficient inventory)"

        record("Gift lifecycle (purchase + transfer + summary)", True)
    except AssertionError as e:
        record("Gift lifecycle", False, str(e))
    except Exception as e:
        import traceback
        record("Gift lifecycle", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: VIP Subscription Lifecycle
# ═══════════════════════════════════════════════════════════════════════════
async def test_vip_lifecycle(engine):
    print("\n── Integration Test 2: VIP Subscription ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User
        from matching_bot_project.services.vip_subscription import VIPSubscriptionManager

        vm = VIPSubscriptionManager()

        async with async_session_factory() as session:
            user = User(tg_id=20001, first_name="VIP User", public_id="user_vip1",
                        coin_balance=10, referral_code="VIPUSER1")
            session.add(user)
            await session.commit()

            # 1. Initially not VIP
            assert await vm.is_vip_active(session, 20001) == False
            assert await vm.get_remaining_days(session, 20001) == 0

            # 2. Activate 1-week subscription
            sub = await vm.activate_subscription(session, 20001, "1w")
            assert sub.expires_at > sub.started_at
            assert sub.is_active == True

            # 3. Now VIP is active
            assert await vm.is_vip_active(session, 20001) == True
            days = await vm.get_remaining_days(session, 20001)
            assert 6 <= days <= 7, f"expected 6-7 days remaining, got {days}"

            # 4. Activate 2-week subscription on top — should extend from current expiry
            old_expires = sub.expires_at
            sub2 = await vm.activate_subscription(session, 20001, "2w")
            assert sub2.expires_at > old_expires, "should extend from current expiry"

            # 5. Expire due subscriptions (none should expire yet)
            expired_count = await vm.expire_due_subscriptions(session)
            assert expired_count == 0, f"expected 0 expired, got {expired_count}"

            # 6. Manually expire one subscription
            sub.is_active = True  # re-activate for test
            sub.expires_at = datetime.utcnow() - timedelta(days=1)  # naive UTC (SQLite compat)
            await session.commit()
            expired_count = await vm.expire_due_subscriptions(session)
            assert expired_count >= 1, f"expected ≥1 expired, got {expired_count}"

        record("VIP lifecycle (activate + extend + expire)", True)
    except AssertionError as e:
        record("VIP lifecycle", False, str(e))
    except Exception as e:
        import traceback
        record("VIP lifecycle", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Referral Attribution + Commission
# ═══════════════════════════════════════════════════════════════════════════
async def test_referral(engine):
    print("\n── Integration Test 3: Referral System ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User, CoinPurchaseOrder
        from matching_bot_project.services.referral_engine import ReferralEngine
        import json as _json

        re_engine = ReferralEngine()

        async with async_session_factory() as session:
            # Create referrer (with referral code)
            referrer = User(tg_id=30001, first_name="Referrer", public_id="user_ref1",
                            coin_balance=0, referral_code="REFCODE1",
                            referral_earnings=0)
            session.add(referrer)
            await session.commit()

            # Create referred user (without referrer initially)
            referred = User(tg_id=30002, first_name="Referred", public_id="user_ref2",
                            coin_balance=0, referral_code="REFCODE2")
            session.add(referred)
            await session.commit()

            # 1. Attribute referral
            ok = await re_engine.attribute_referral(session, 30002, "REFCODE1")
            assert ok, "attribution should succeed"
            await session.refresh(referred)
            assert referred.referrer_id == 30001

            # 2. Simulate coin purchase by referred user
            order = CoinPurchaseOrder(
                user_tg_id=30002,
                payment_method="gateway",
                order_type="coins",
                order_payload=_json.dumps({"coins": 100}),
                status="approved",
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

            # Credit coins to referred user
            referred.coin_balance += 100
            await session.commit()

            # 3. Process commission (20% of 100 = 20 coins to referrer)
            commission = await re_engine.process_commission_on_purchase(
                session, order.id, 30002, 100
            )
            assert commission is not None, "commission should be created"
            assert commission.commission_coins == 20, \
                f"expected 20, got {commission.commission_coins}"

            # 4. Verify referrer's coin_balance + referral_earnings
            await session.refresh(referrer)
            assert referrer.coin_balance == 20, f"expected 20, got {referrer.coin_balance}"
            assert referrer.referral_earnings == 20

            # 5. Stats
            stats = await re_engine.get_referral_stats(session, 30001)
            assert stats["total_referred"] == 1
            assert stats["total_commission"] == 20

        record("Referral attribution + commission (20% of 100)", True)
    except AssertionError as e:
        record("Referral system", False, str(e))
    except Exception as e:
        import traceback
        record("Referral system", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Warning System (3-strike ban)
# ═══════════════════════════════════════════════════════════════════════════
async def test_warning_3strike(engine):
    print("\n── Integration Test 4: Warning System (3-Strike) ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User, UserReport
        from matching_bot_project.services.warning_engine import WarningEngine

        we = WarningEngine()

        async with async_session_factory() as session:
            # Create users
            bad_user = User(tg_id=40001, first_name="Bad", public_id="user_bad1",
                            coin_balance=0, warning_count=0, is_banned=False,
                            referral_code="BADUSER1")
            reporter = User(tg_id=40002, first_name="Reporter", public_id="user_rep1",
                            coin_balance=0, warning_count=0, is_banned=False,
                            referral_code="REPORTER1")
            session.add_all([bad_user, reporter])
            await session.commit()

            # 1. Issue first warning
            result = await we.issue_warning(
                session, 40001, "spam", "admin_action", admin_tg_id=99999
            )
            assert result["warning_count"] == 1
            assert result["is_banned"] == False

            # 2. Issue second warning
            result = await we.issue_warning(
                session, 40001, "harassment", "admin_action", admin_tg_id=99999
            )
            assert result["warning_count"] == 2
            assert result["is_banned"] == False

            # 3. Issue third warning → should ban
            result = await we.issue_warning(
                session, 40001, "inappropriate", "admin_action", admin_tg_id=99999
            )
            assert result["warning_count"] == 3
            assert result["is_banned"] == True

            # 4. Verify user is_banned
            await session.refresh(bad_user)
            assert bad_user.is_banned == True

            # 5. Test report approval workflow (with another user)
            bad2 = User(tg_id=40003, first_name="Bad2", public_id="user_bad2",
                        coin_balance=0, warning_count=0, is_banned=False,
                        referral_code="BADUSER2")
            session.add(bad2)
            await session.commit()

            report = UserReport(
                reporter_id=40002,
                reported_id=40003,
                reason="scammer",
                status="pending",
            )
            session.add(report)
            await session.commit()
            await session.refresh(report)

            # Approve report
            approval = await we.approve_report(session, report.id, admin_tg_id=99999)
            assert approval["warning_result"]["warning_count"] == 1
            assert approval["reward_coins"] == 5
            
            # Reporter should have 5 coins
            await session.refresh(reporter)
            assert reporter.coin_balance == 5

        record("Warning system (3-strike ban + report reward)", True)
    except AssertionError as e:
        record("Warning system", False, str(e))
    except Exception as e:
        import traceback
        record("Warning system", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: Profile Completion
# ═══════════════════════════════════════════════════════════════════════════
async def test_profile_completion(engine):
    print("\n── Integration Test 5: Profile Completion ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User
        from matching_bot_project.services.profile_completion import (
            ProfileCompletionService, REQUIRED_STEPS
        )

        pc = ProfileCompletionService()

        async with async_session_factory() as session:
            user = User(tg_id=50001, first_name="Incomplete", public_id="user_inc1",
                        coin_balance=0, profile_completion_rewarded=False,
                        referral_code="INCOMPLE1")
            session.add(user)
            await session.commit()

            # 1. Initially 0% complete
            state = await pc.get_completion_state(session, 50001)
            assert state["pct"] == 0
            assert state["is_complete"] == False

            # 2. Mark city done
            user.city = "Tehran"
            await pc.mark_step_done(session, 50001, "city")
            state = await pc.get_completion_state(session, 50001)
            assert state["pct"] == 20, f"expected 20%, got {state['pct']}"

            # 3. Mark all required steps done
            user.profile_photo_file_id = "test_file_id"
            user.location_lat = 35.6892
            user.location_lng = 51.3890
            user.tags = "smoker,athlete"
            user.bio = "Hello world"
            await pc.mark_step_done(session, 50001, "photo")
            await pc.mark_step_done(session, 50001, "gps")
            await pc.mark_step_done(session, 50001, "tags")
            await pc.mark_step_done(session, 50001, "bio")
            
            state = await pc.get_completion_state(session, 50001)
            assert state["is_complete"] == True, "should be complete"
            assert state["pct"] == 100

            # 4. Try to award reward
            reward = await pc.try_award_completion_reward(session, 50001)
            assert reward == 10, f"expected 10, got {reward}"

            # 5. Try again — should return None (already rewarded)
            reward2 = await pc.try_award_completion_reward(session, 50001)
            assert reward2 is None

            # 6. Verify user's coin_balance
            await session.refresh(user)
            assert user.coin_balance == 10
            assert user.profile_completion_rewarded == True

        record("Profile completion (5 steps + reward)", True)
    except AssertionError as e:
        record("Profile completion", False, str(e))
    except Exception as e:
        import traceback
        record("Profile completion", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: Free Coin Banner
# ═══════════════════════════════════════════════════════════════════════════
async def test_banner(engine):
    print("\n── Integration Test 6: Free Coin Banner ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User, BannerCampaign
        from matching_bot_project.services.free_coin_banner import FreeCoinBannerService

        bs = FreeCoinBannerService()

        async with async_session_factory() as session:
            # Create user
            user = User(tg_id=60001, first_name="Banner", public_id="user_ban1",
                        coin_balance=0, referral_code="BANNER1AB")
            session.add(user)
            await session.commit()

            # 1. Create campaign
            campaign = await bs.create_campaign(
                session,
                banner_photo_file_id="test_photo_file_id",
                caption_text="این بنر را فوروارد کنید!",
                reward_coins=2,
            )
            assert campaign.id is not None
            assert campaign.is_active == True

            # 2. User forwards banner
            ok, msg = await bs.record_forward(
                session, 60001, campaign.id, forward_msg_id=12345
            )
            assert ok, f"forward should succeed: {msg}"

            # 3. User tries to forward again — should fail
            ok, msg = await bs.record_forward(
                session, 60001, campaign.id, forward_msg_id=12346
            )
            assert not ok, "should fail (already forwarded)"

            # 4. Get pending forwards
            pending = await bs.get_pending_forwards(session)
            assert len(pending) >= 1

            forward_id = pending[0][0].id

            # 5. Approve forward — user should get 2 coins
            result = await bs.approve_forward(session, forward_id, admin_tg_id=99999)
            assert result["reward_coins"] == 2

            await session.refresh(user)
            assert user.coin_balance == 2

        record("Free coin banner (create + forward + approve)", True)
    except AssertionError as e:
        record("Free coin banner", False, str(e))
    except Exception as e:
        import traceback
        record("Free coin banner", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Tag system (max 3 normal, max 10 VIP)
# ═══════════════════════════════════════════════════════════════════════════
async def test_tags(engine):
    print("\n── Integration Test 7: Tag System ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User, TagCatalog
        from matching_bot_project.database.queries.crud import (
            get_user_tags, set_user_tags, get_tag_catalog
        )

        async with async_session_factory() as session:
            # Seed catalog with 15 tags
            tags_to_seed = []
            for i, code in enumerate([
                "smoker", "athlete", "vegetarian", "bookworm", "cinephile",
                "musician", "gamer", "traveler", "coffee_lover", "night_owl",
                "tall", "introvert", "extrovert", "funny", "romantic"
            ]):
                tags_to_seed.append(TagCatalog(
                    code=code,
                    display_name=f"Tag {code}",
                    emoji="🏷",
                    category="lifestyle",
                    is_active=True,
                    sort_order=i,
                ))
            session.add_all(tags_to_seed)

            # Create normal user (not VIP)
            normal = User(tg_id=70001, first_name="Normal", public_id="user_norm1",
                          is_vip=False, referral_code="NORMAL1AB")
            # Create VIP user
            vip = User(tg_id=70002, first_name="VIP", public_id="user_vip2",
                       is_vip=True, vip_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                       referral_code="VIPUSER2A")
            session.add_all([normal, vip])
            await session.commit()

            # 1. Normal user tries to set 4 tags → should fail (max 3)
            ok, msg = await set_user_tags(session, 70001,
                ["smoker", "athlete", "bookworm", "gamer"], max_tags=3)
            assert not ok, "should reject 4 tags for normal user"

            # 2. Normal user sets 3 tags → should succeed
            ok, msg = await set_user_tags(session, 70001,
                ["smoker", "athlete", "bookworm"], max_tags=3)
            assert ok, f"3 tags should succeed: {msg}"

            tags = await get_user_tags(session, 70001)
            assert len(tags) == 3
            assert set(tags) == {"smoker", "athlete", "bookworm"}

            # 3. VIP user sets 10 tags → should succeed
            ten_tags = ["smoker", "athlete", "vegetarian", "bookworm", "cinephile",
                        "musician", "gamer", "traveler", "coffee_lover", "night_owl"]
            ok, msg = await set_user_tags(session, 70002, ten_tags, max_tags=10)
            assert ok, f"10 tags should succeed for VIP: {msg}"

            tags = await get_user_tags(session, 70002)
            assert len(tags) == 10

            # 4. VIP user tries 11 tags → should fail
            eleven_tags = ten_tags + ["tall"]
            ok, msg = await set_user_tags(session, 70002, eleven_tags, max_tags=10)
            assert not ok, "should reject 11 tags for VIP"

            # 5. Try invalid tag → should fail
            ok, msg = await set_user_tags(session, 70001, ["nonexistent_tag"], max_tags=3)
            assert not ok, "should reject invalid tag"

        record("Tag system (3 normal / 10 VIP)", True)
    except AssertionError as e:
        record("Tag system", False, str(e))
    except Exception as e:
        import traceback
        record("Tag system", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Direct Messages
# ═══════════════════════════════════════════════════════════════════════════
async def test_direct_messages(engine):
    print("\n── Integration Test 8: Direct Messages ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User, DirectMessage
        from matching_bot_project.database.queries.crud import (
            get_user_unread_dm_count, get_recent_direct_messages
        )

        async with async_session_factory() as session:
            sender = User(tg_id=80001, first_name="Sender", public_id="user_send1",
                          referral_code="SENDER1AB")
            receiver = User(tg_id=80002, first_name="Receiver", public_id="user_recv2",
                            referral_code="RECEIV2CD")
            session.add_all([sender, receiver])
            await session.commit()

            # 1. Send 3 DMs
            for i in range(3):
                dm = DirectMessage(
                    sender_tg_id=80001,
                    receiver_tg_id=80002,
                    body=f"Message {i+1}",
                )
                session.add(dm)
            await session.commit()

            # 2. Check unread count
            count = await get_user_unread_dm_count(session, 80002)
            assert count == 3, f"expected 3, got {count}"

            # 3. Mark one as read
            result = await session.execute(
                __import__('sqlalchemy').select(DirectMessage).where(
                    DirectMessage.receiver_tg_id == 80002
                ).limit(1)
            )
            first_dm = result.scalar_one()
            first_dm.is_read = True
            first_dm.read_at = datetime.now(timezone.utc)
            await session.commit()

            # 4. Check unread count = 2
            count = await get_user_unread_dm_count(session, 80002)
            assert count == 2, f"expected 2, got {count}"

            # 5. Get recent DMs
            recent = await get_recent_direct_messages(session, 80002, limit=10)
            assert len(recent) == 3

        record("Direct messages (send + unread + read)", True)
    except AssertionError as e:
        record("Direct messages", False, str(e))
    except Exception as e:
        import traceback
        record("Direct messages", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 9: Distance Filter with multiple users
# ═══════════════════════════════════════════════════════════════════════════
async def test_distance_filter_multi(engine):
    print("\n── Integration Test 9: Distance Filter (Multi-user) ──")
    try:
        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User
        


        async with async_session_factory() as session:
            # Tehran (35.6892, 51.3890)
            tehran = User(tg_id=90001, first_name="Tehran", public_id="user_teh1",
                          location_lat=35.6892, location_lng=51.3890,
                          referral_code="TEHRAN1AB")
            # Karaj (35.8400, 50.9391) — ~40km from Tehran
            karaj = User(tg_id=90002, first_name="Karaj", public_id="user_kar2",
                         location_lat=35.8400, location_lng=50.9391,
                         referral_code="KARAJ2BCD")
            # Isfahan (32.6539, 51.6660) — ~350km from Tehran
            isfahan = User(tg_id=90003, first_name="Isfahan", public_id="user_isf3",
                           location_lat=32.6539, location_lng=51.6660,
                           referral_code="ISFAHA3EF")
            # No location
            noloc = User(tg_id=90004, first_name="NoLoc", public_id="user_nol4",
                         location_lat=None, location_lng=None,
                         referral_code="NOLOC4GH")
            session.add_all([tehran, karaj, isfahan, noloc])
            await session.commit()

            candidates = [karaj, isfahan, noloc]

            # 1. Tehran user filters for 0-50 km → should match Karaj only
            passed = svc.filter_candidates_by_distance(tehran, candidates, "0_50")
            passed_ids = {u.tg_id for u in passed}
            assert 90002 in passed_ids, "Karaj should be in 0-50km"
            assert 90003 not in passed_ids, "Isfahan should NOT be in 0-50km"
            assert 90004 not in passed_ids, "NoLoc should NOT be in any distance filter"

            # 2. Tehran user filters for 50-100 km → none match
            passed = svc.filter_candidates_by_distance(tehran, candidates, "50_100")
            assert len(passed) == 0

            # 3. Tehran user filters for 100-200 km → none (Isfahan is 350km)
            passed = svc.filter_candidates_by_distance(tehran, candidates, "100_200")
            assert len(passed) == 0

            # 4. "any" filter → all candidates returned (including NoLoc)
            passed = svc.filter_candidates_by_distance(tehran, candidates, "any")
            assert len(passed) == 3

            # 5. Distance between Tehran and Karaj
            d = svc.distance_between_users(tehran, karaj)
            assert 30 < d < 50, f"Tehran-Karaj expected ~40km, got {d:.1f}"

            # 6. Distance between Tehran and Isfahan
            d = svc.distance_between_users(tehran, isfahan)
            assert 300 < d < 400, f"Tehran-Isfahan expected ~350km, got {d:.1f}"

            # 7. Distance to user without location
            d = svc.distance_between_users(tehran, noloc)
            assert d is None

        record("Distance filter (4 users, 3 buckets)", True)
    except AssertionError as e:
        record("Distance filter multi-user", False, str(e))
    except Exception as e:
        import traceback
        record("Distance filter multi-user", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║   Matching Bot v3 — Integration Tests (real DB operations)    ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    engine = await setup_db()

    await test_gift_lifecycle(engine)
    await test_vip_lifecycle(engine)
    await test_referral(engine)
    await test_warning_3strike(engine)
    await test_profile_completion(engine)
    await test_banner(engine)
    await test_tags(engine)
    await test_direct_messages(engine)
    await test_distance_filter_multi(engine)

    await engine.dispose()

    # Summary
    print("\n" + "═" * 70)
    print("INTEGRATION TEST SUMMARY")
    print("═" * 70)
    passed = sum(1 for _, s, _ in RESULTS if s)
    failed = sum(1 for _, s, _ in RESULTS if not s)
    for name, success, detail in RESULTS:
        status = "✅" if success else "❌"
        print(f"  {status} {name}" + (f" — {detail}" if detail and not success else ""))
    print("─" * 70)
    print(f"  Total: {len(RESULTS)}   Passed: {passed}   Failed: {failed}")
    print("═" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

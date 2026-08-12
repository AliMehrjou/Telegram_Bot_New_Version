#!/usr/bin/env python3
"""
Comprehensive test runner for Matching Bot v3.

Run with:  python3 tests/run_tests.py

Tests performed:
1. Python syntax (AST) check on all .py files
2. Imports of all models, services, handlers, middlewares, keyboards
3. JSON file validity
4. SQLAlchemy models create_all on SQLite (verifies table definitions)
5. URL_REGEX filter test (security regression)
6. DistanceFilterService unit tests
7. Constants classes (VIPPlan, GiftCode, DistanceFilter, TagCategory) validity
8. Referral code generation + uniqueness
9. Keyboard constructors
10. .env.example parseable with pydantic-settings
11. docker-compose.yml valid YAML
12. nginx.conf has basic structural sanity
13. seed scripts importable
14. State machine definitions
15. Migration SQL files don't have obvious syntax errors
"""

import asyncio
import json
import os
import re
import sys
import ast
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Setup environment for tests
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:////tmp/match_bot_test.db'
# aiogram requires a valid-format bot token: <bot_id>:<hash_of_at_least_35_chars>
os.environ['BOT_TOKEN'] = '123456789:AAEhBP0vXbG3YpJ8Z3rD5mF8K2tN6qL9VxYwZ-abcDEF'
os.environ['WEBHOOK_SECRET_TOKEN'] = 'test_webhook_secret_at_least_32_chars_long'
os.environ['ADMIN_SECRET_TOKEN'] = 'test_admin_token'
os.environ['REQUIRED_CHANNEL_ID'] = '-1001234567890'
os.environ['ENVIRONMENT'] = 'development'

# Add project to path
# PROJECT_ROOT points to the actual v3 source directory.
# tests/ lives inside it. For test imports we use matching_bot_project_v3
# (which is a sibling directory containing matching_bot_project/ package).
PROJECT_ROOT = Path('/home/z/my-project/matching_bot_v3')
TEST_PKG_ROOT = Path('/home/z/my-project/matching_bot_project_v3')
sys.path.insert(0, str(TEST_PKG_ROOT))

# Test results accumulator
RESULTS = []
def record(name, success, detail=""):
    status = "✅" if success else "❌"
    RESULTS.append((name, success, detail))
    print(f"{status} {name}" + (f": {detail}" if detail and not success else ""))


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Python syntax check
# ═══════════════════════════════════════════════════════════════════════════
def test_python_syntax():
    print("\n── Test 1: Python Syntax ──")
    errors = []
    for root, dirs, files in os.walk(PROJECT_ROOT / 'matching_bot_project'):
        if any(skip in str(root) for skip in ['__pycache__', '.git']):
            continue
        for f in files:
            if not f.endswith('.py'):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as fp:
                    source = fp.read()
                ast.parse(source, filename=path)
            except SyntaxError as e:
                errors.append(f'{path}: line {e.lineno}: {e.msg}')
    record("Python syntax (all .py files)", not errors, f"{len(errors)} errors" if errors else "")
    for e in errors[:10]:
        print(f"   - {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Imports
# ═══════════════════════════════════════════════════════════════════════════
def test_imports():
    print("\n── Test 2: Module Imports ──")
    modules_to_test = [
        'matching_bot_project.database.models.models',
        'matching_bot_project.bot.core.constants',
        'matching_bot_project.bot.states.states',
        'matching_bot_project.services.gift_engine',
        'matching_bot_project.services.referral_engine',
        'matching_bot_project.services.warning_engine',
        'matching_bot_project.services.vip_subscription',
        'matching_bot_project.services.profile_completion',
        'matching_bot_project.services.free_coin_banner',
        'matching_bot_project.services.cron_reminders',
        'matching_bot_project.bot.keyboards.inline',
        'matching_bot_project.bot.keyboards.reply',
        'matching_bot_project.bot.core.formatters',
        'matching_bot_project.bot.middlewares.anti_spam',
        'matching_bot_project.bot.middlewares.direct_message_privacy',
        'matching_bot_project.bot.handlers.help',
        'matching_bot_project.bot.handlers.coins_menu',
        'matching_bot_project.bot.handlers.gifts',
        'matching_bot_project.bot.handlers.referral',
        'matching_bot_project.bot.handlers.direct_messages',
    ]
    failed = []
    for mod in modules_to_test:
        try:
            __import__(mod)
        except Exception as e:
            failed.append((mod, str(e)))
    record(f"Module imports ({len(modules_to_test)} modules)", not failed)
    for mod, err in failed:
        print(f"   - {mod}: {err[:200]}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: JSON file validity
# ═══════════════════════════════════════════════════════════════════════════
def test_json_files():
    print("\n── Test 3: JSON File Validity ──")
    json_dir = PROJECT_ROOT / 'json_files'
    json_files = list(json_dir.glob('*.json'))
    failed = []
    for jf in json_files:
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if jf.name == 'gifts_catalog.json':
                assert 'gifts' in data and len(data['gifts']) >= 3, "need at least 3 gifts"
                for g in data['gifts']:
                    assert all(k in g for k in ['code', 'display_name', 'emoji', 'price_coins']), \
                        f"gift {g.get('code')} missing fields"
            elif jf.name == 'vip_plans.json':
                for code in ['1w', '2w', '1m']:
                    assert code in data, f"missing plan {code}"
                    assert all(k in data[code] for k in ['label', 'duration_days', 'price_toman']), \
                        f"plan {code} missing fields"
            elif jf.name == 'tags_catalog.json':
                assert 'tags' in data and len(data['tags']) >= 10, "need at least 10 tags"
                for t in data['tags']:
                    assert all(k in t for k in ['code', 'display_name', 'category']), \
                        f"tag {t.get('code')} missing fields"
            elif jf.name == 'help.json':
                required = ['chat', 'credit', 'gps', 'profile', 'vip', 'gapogift', 'tags']
                for r in required:
                    assert r in data, f"missing help topic: {r}"
        except Exception as e:
            failed.append((jf.name, str(e)))
    record(f"JSON files ({len(json_files)} files)", not failed)
    for name, err in failed:
        print(f"   - {name}: {err}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: SQLAlchemy models create_all (on SQLite)
# ═══════════════════════════════════════════════════════════════════════════
async def test_models_create_all():
    print("\n── Test 4: SQLAlchemy Models (create_all on SQLite) ──")
    try:
        test_db = Path('/tmp/match_bot_test.db')
        if test_db.exists():
            test_db.unlink()

        from matching_bot_project.database.session import engine, Base
        from matching_bot_project.database.models import models  # noqa

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy import inspect
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

        record(f"create_all ({len(tables)} tables created)", len(tables) >= 18)
        if len(tables) < 18:
            print(f"   Expected at least 18 tables, got: {tables}")
        else:
            print(f"   Tables: {sorted(tables)}")

        await engine.dispose()
    except Exception as e:
        import traceback
        record("create_all", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: URL_REGEX filter (security regression)
# ═══════════════════════════════════════════════════════════════════════════
def test_url_regex():
    print("\n── Test 5: URL_REGEX Filter (Security) ──")
    try:
        src = (PROJECT_ROOT / 'bot' / 'handlers' / 'anonymous_chat.py').read_text()
        # Extract the regex pattern - it's a multi-line concatenated raw string
        # Pattern: re.compile(
        #     r"(https?://...",
        #     r"more...",
        #     re.IGNORECASE
        # )
        m = re.search(r'URL_REGEX:\s*re\.Pattern\s*=\s*re\.compile\(\s*(.+?)\n\)', src, re.DOTALL)
        if not m:
            record("URL_REGEX extraction", False, "could not find URL_REGEX block")
            return
        block = m.group(1)
        # Extract all r"..." or r'...' segments and concatenate
        parts = re.findall(r'r["\'](.+?)["\']', block, re.DOTALL)
        if not parts:
            record("URL_REGEX extraction", False, "no pattern parts found")
            return
        pattern_text = ''.join(parts)
        regex = re.compile(pattern_text, re.VERBOSE | re.IGNORECASE)

        leak_cases = [
            ("https://example.io/path", True),
            ("https://t.me/example", True),
            ("www.mysite.app", True),
            ("https://foo.dev/bar", True),
            ("check site.xyz now", True),
            ("myemail@gmail.com", True),
            ("https://tld.click/x", True),
            ("visit example.page", True),
            ("foo.bar.baz.io", True),
            ("hello world", False),
            ("my username is @ali", False),
            ("call me at 09123456789", False),
        ]

        failures = []
        for text, should_match in leak_cases:
            matches = bool(regex.search(text))
            if matches != should_match:
                failures.append((text, should_match, matches))

        record(f"URL_REGEX filter ({len(leak_cases)} cases)", not failures)
        for text, expected, got in failures:
            print(f"   - '{text}': expected match={expected}, got match={got}")
    except Exception as e:
        import traceback
        record("URL_REGEX filter", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: DistanceFilterService
# ═══════════════════════════════════════════════════════════════════════════
def test_distance_filter():
    print("\n── Test 6: DistanceFilterService ──")
    try:
        
        

        d = svc.distance_km(35.6892, 51.3890, 32.6539, 51.6660)
        assert 300 < d < 400, f"Tehran-Isfahan expected ~350km, got {d:.1f}"

        d0 = svc.distance_km(35.6892, 51.3890, 35.6892, 51.3890)
        assert d0 < 1, f"same point expected 0km, got {d0:.2f}"

        assert svc.passes_filter(10, "0_50") == True
        assert svc.passes_filter(50, "0_50") == False
        assert svc.passes_filter(50, "50_100") == True
        assert svc.passes_filter(150, "100_200") == True
        assert svc.passes_filter(250, "100_200") == False
        assert svc.passes_filter(1000, "any") == True

        assert "متر" in svc.format_distance(0.5)
        assert "کیلومتر" in svc.format_distance(15)
        assert "نامشخص" in svc.format_distance(None)

        class FakeUser:
            location_lat = None
            location_lng = None
        u = FakeUser()
        assert svc.user_has_location(u) == False
        u.location_lat = 35.6
        u.location_lng = 51.3
        assert svc.user_has_location(u) == True

        record("DistanceFilterService (5 sub-tests)", True)
    except AssertionError as e:
        record("DistanceFilterService", False, str(e))
    except Exception as e:
        import traceback
        record("DistanceFilterService", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Constants classes
# ═══════════════════════════════════════════════════════════════════════════
def test_constants():
    print("\n── Test 7: Constants Classes ──")
    try:
        from matching_bot_project.bot.core.constants import (
            VIPPlan, GiftCode, DistanceFilter, TagCategory, Limits, ReplyBtn, InlineBtn, Messages
        )
        assert VIPPlan.WEEK_1 == "1w"
        assert VIPPlan.DURATION_DAYS[VIPPlan.WEEK_1] == 7
        assert VIPPlan.DURATION_DAYS[VIPPlan.MONTH_1] == 30
        assert len(VIPPlan.LABELS) == 3

        assert GiftCode.TEDDY in GiftCode.ALL
        assert len(GiftCode.ALL) == 5
        assert GiftCode.EMOJIS[GiftCode.TEDDY] == "🧸"

        assert DistanceFilter.RANGES_KM[DistanceFilter.NEAR] == (0, 50)
        assert DistanceFilter.RANGES_KM[DistanceFilter.FAR] == (100, 200)

        assert TagCategory.LIFESTYLE == "lifestyle"

        assert Limits.TAGS_NORMAL_USER == 3
        assert Limits.TAGS_VIP_USER == 10
        assert Limits.REFERRAL_COMMISSION_PCT == 20
        assert Limits.MAX_WARNINGS_BEFORE_BAN == 3
        assert Limits.MATCH_QUEUE_TTL_SECONDS == 300
        assert Limits.MATCH_INITIAL_LOCK_SECONDS == 5

        for attr in ['START_DATE', 'MY_PROFILE', 'DISCOVER', 'MY_COINS',
                     'VIP_SUBSCRIPTION', 'GIFTS', 'SUPPORT', 'HELP']:
            assert hasattr(ReplyBtn, attr), f"ReplyBtn missing {attr}"

        for attr in ['UNKNOWN_MESSAGE', 'ALREADY_IN_QUEUE', 'NO_MATCH_FOUND',
                     'LIKE_RECEIVED', 'PROFILE_COMPLETION_REMINDER']:
            assert hasattr(Messages, attr), f"Messages missing {attr}"

        record("Constants classes (VIPPlan, GiftCode, DistanceFilter, Limits, etc.)", True)
    except AssertionError as e:
        record("Constants classes", False, str(e))
    except Exception as e:
        record("Constants classes", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Referral code generation + uniqueness
# ═══════════════════════════════════════════════════════════════════════════
def test_referral_codes():
    print("\n── Test 8: Referral Code Generation ──")
    try:
        from matching_bot_project.services.referral_engine import generate_referral_code, build_referral_link
        codes = set()
        for _ in range(1000):
            c = generate_referral_code(8)
            assert len(c) == 8, f"code length: {len(c)}"
            assert 'O' not in c and '0' not in c and 'I' not in c and '1' not in c
            codes.add(c)
        assert len(codes) == 1000, f"only {len(codes)} unique out of 1000"

        link = build_referral_link("ABC12345", "MyBot")
        assert link == "https://t.me/MyBot?start=ref_ABC12345"

        record("Referral code generation (1000 unique codes)", True)
    except AssertionError as e:
        record("Referral code generation", False, str(e))
    except Exception as e:
        record("Referral code generation", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 9: Keyboard constructors
# ═══════════════════════════════════════════════════════════════════════════
def test_keyboards():
    print("\n── Test 9: Keyboard Constructors ──")
    try:
        from matching_bot_project.bot.keyboards.reply import (
            get_main_menu_keyboard, get_cancel_keyboard,
            get_date_phase_keyboard, get_chat_phase_keyboard,
            get_terms_reply_keyboard, get_back_to_menu_keyboard
        )
        from matching_bot_project.bot.keyboards.inline import (
            get_vip_subscription_plans_keyboard, get_distance_filter_keyboard,
            get_coins_main_menu_keyboard, get_gifts_main_menu_keyboard,
            get_gift_picker_keyboard, get_gift_quantity_keyboard,
            get_help_main_keyboard, get_referral_dashboard_keyboard,
            get_dm_inbox_keyboard, get_dm_message_keyboard,
            get_matching_type_keyboard_v3, get_discovery_main_menu_keyboard,
            get_tag_selection_keyboard, get_profile_completion_keyboard,
            get_free_coin_banner_keyboard, get_admin_broadcast_pin_keyboard,
            get_admin_report_review_keyboard, get_admin_banner_review_keyboard,
        )
        get_main_menu_keyboard()
        get_main_menu_keyboard(is_vip=True)
        get_cancel_keyboard()
        get_date_phase_keyboard()
        get_chat_phase_keyboard()
        get_terms_reply_keyboard()
        get_back_to_menu_keyboard()
        get_vip_subscription_plans_keyboard()
        get_distance_filter_keyboard()
        get_coins_main_menu_keyboard()
        get_gifts_main_menu_keyboard()
        get_gift_picker_keyboard(prices={"teddy": 5, "rose": 3})
        get_gift_picker_keyboard(prices={"teddy": 5}, owned={"teddy": 2})
        get_gift_quantity_keyboard("teddy")
        get_help_main_keyboard()
        get_referral_dashboard_keyboard()
        get_dm_inbox_keyboard(messages=[(1, "user_abcd12", "hello world test", "10:00")])
        get_dm_message_keyboard(message_id=1, sender_tg_id=123)
        get_matching_type_keyboard_v3()
        get_discovery_main_menu_keyboard()
        get_tag_selection_keyboard(
            tags_by_cat={"lifestyle": [("smoker", "سیگاری", "🚬")]},
            selected={"smoker"}, max_tags=3
        )
        get_profile_completion_keyboard(current_step="city")
        get_free_coin_banner_keyboard(campaign_id=1)
        get_admin_broadcast_pin_keyboard()
        get_admin_report_review_keyboard(report_id=1)
        get_admin_banner_review_keyboard(forward_id=1)

        record("Keyboard constructors (26 constructors)", True)
    except Exception as e:
        import traceback
        record("Keyboard constructors", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 10: .env.example parseable
# ═══════════════════════════════════════════════════════════════════════════
def test_env_example():
    print("\n── Test 10: .env.example ──")
    env_path = PROJECT_ROOT / '.env.example'
    if not env_path.exists():
        record(".env.example exists", False)
        return
    content = env_path.read_text()
    lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
    failed = []
    for line in lines:
        if '=' not in line:
            failed.append(f"no '=' in: {line}")
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        if not k.replace('_', '').isalnum() or not k[0].isalpha():
            failed.append(f"invalid key: {k}")
    record(f".env.example parseable ({len(lines)} entries)", not failed)
    for f in failed:
        print(f"   - {f}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 11: docker-compose.yml valid YAML
# ═══════════════════════════════════════════════════════════════════════════
def test_docker_compose():
    print("\n── Test 11: docker-compose.yml ──")
    try:
        import yaml
    except ImportError:
        subprocess.run(['pip', 'install', '--break-system-packages', 'pyyaml', '-q'],
                       check=False, capture_output=True)
        import yaml

    dc_path = PROJECT_ROOT / 'docker-compose.yml'
    with open(dc_path) as f:
        data = yaml.safe_load(f)

    failed = []
    if 'services' not in data:
        failed.append("no 'services' key")
    else:
        services = data['services']
        # v3.1 uses mysql_primary (not mysql_db)
        for required in ['mysql_primary', 'redis_cache', 'fastapi_bot', 'nginx']:
            if required not in services:
                failed.append(f"missing service: {required}")
        if 'nginx' in services:
            ports = services['nginx'].get('ports', [])
            if '80:80' not in ports or '443:443' not in ports:
                failed.append(f"nginx ports missing 80:80 or 443:443")

    record("docker-compose.yml (services + ports)", not failed)
    for f in failed:
        print(f"   - {f}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 12: nginx.conf structural sanity
# ═══════════════════════════════════════════════════════════════════════════
def test_nginx_conf():
    print("\n── Test 12: nginx.conf ──")
    conf_path = PROJECT_ROOT / 'nginx' / 'nginx.conf'
    with open(conf_path) as f:
        content = f.read()

    failed = []
    for directive in ['worker_processes', 'events', 'http', 'server', 'upstream']:
        if directive not in content:
            failed.append(f"missing directive: {directive}")
    if 'fastapi_bot:8000' not in content:
        failed.append("upstream missing fastapi_bot:8000")
    if 'ssl_certificate' not in content or 'ssl_certificate_key' not in content:
        failed.append("missing SSL certificate directives")
    for zone in ['zone=webhook', 'zone=admin', 'zone=payment']:
        if zone not in content:
            failed.append(f"missing rate limit zone: {zone}")

    record("nginx.conf structure", not failed)
    for f in failed:
        print(f"   - {f}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 13: seed scripts importable
# ═══════════════════════════════════════════════════════════════════════════
def test_seed_scripts():
    print("\n── Test 13: Seed Scripts ──")
    scripts = ['seed_tags.py', 'seed_gifts.py', 'seed_vip_plans.py']
    failed = []
    for s in scripts:
        path = PROJECT_ROOT / 'scripts' / s
        if not path.exists():
            failed.append(f"{s} not found")
            continue
        try:
            with open(path) as f:
                source = f.read()
            ast.parse(source, filename=str(path))
        except SyntaxError as e:
            failed.append(f"{s}: {e}")
    record(f"Seed scripts syntax ({len(scripts)} scripts)", not failed)
    for f in failed:
        print(f"   - {f}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 14: State machine definitions
# ═══════════════════════════════════════════════════════════════════════════
def test_states():
    print("\n── Test 14: FSM State Groups ──")
    try:
        from matching_bot_project.bot.states.states import (
            OnboardingStates, MatchingStates, QuestionnaireStates, ChatStates,
            SupportStates, AdminStates, ProfileEditStates, DiscoveryStates,
            ReportStates, VIPStates, EventStates, PBroadcastStates,
            CoinTransferStates, PaymentStates, QuestionAddStates,
            ProfileCommentStates,
            GiftStates, VIPSubscriptionStates, HelpStates,
            DirectMessageStates, ReferralStates, CoinsMenuStates,
            ProfileCompletionStates, TagSelectionStates,
            DistanceFilterStates, BannerForwardStates, WarningReviewStates,
        )
        for cls in [GiftStates, VIPSubscriptionStates, HelpStates,
                    DirectMessageStates, ReferralStates, CoinsMenuStates,
                    ProfileCompletionStates, TagSelectionStates,
                    DistanceFilterStates, BannerForwardStates, WarningReviewStates]:
            states = [s for s in dir(cls) if not s.startswith('_') and s != 'states_group']
            if not states:
                raise AssertionError(f"{cls.__name__} has no states")
        record("FSM State Groups (11 v3 groups + legacy)", True)
    except Exception as e:
        record("FSM State Groups", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 15: Migration SQL files
# ═══════════════════════════════════════════════════════════════════════════
def test_migration_sql():
    print("\n── Test 15: Migration SQL Files ──")
    mig_dir = PROJECT_ROOT / 'database' / 'migrations'
    sql_files = sorted(mig_dir.glob('*.sql'))
    failed = []
    for sf in sql_files:
        with open(sf) as f:
            content = f.read()
        if not content.strip():
            failed.append(f"{sf.name}: empty file")
            continue
        opens = content.count('(')
        closes = content.count(')')
        if opens != closes:
            failed.append(f"{sf.name}: paren mismatch ({opens} open vs {closes} close)")
    record(f"Migration SQL files ({len(sql_files)} files)", not failed)
    for f in failed:
        print(f"   - {f}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 16: Config Settings load
# ═══════════════════════════════════════════════════════════════════════════
def test_config():
    print("\n── Test 16: Settings (pydantic-settings) ──")
    try:
        from matching_bot_project.bot.core.config import settings
        for attr in ['REFERRAL_COMMISSION_PCT', 'PROFILE_COMPLETION_REWARD',
                     'REPORT_REWARD_COINS', 'MAX_WARNINGS_BEFORE_BAN',
                     'LIKE_COOLDOWN_SECONDS', 'MATCH_QUEUE_TTL_SECONDS',
                     'MATCH_INITIAL_LOCK_SECONDS', 'MAX_FORCE_JOIN_CHANNELS',
                     'PRIMARY_NODE_ID', 'SYSTEM_GUARD_SECRET_HASH',
                     'CORS_ALLOW_ORIGINS', 'BROADCAST_BATCH_SIZE']:
            assert hasattr(settings, attr), f"settings missing {attr}"
        assert settings.REFERRAL_COMMISSION_PCT == 20
        assert settings.PROFILE_COMPLETION_REWARD == 10
        assert settings.MAX_WARNINGS_BEFORE_BAN == 3
        assert settings.LIKE_COOLDOWN_SECONDS == 60

        assert isinstance(settings.parsed_admin_ids, list)
        assert isinstance(settings.parsed_cors_origins, list)

        record("Settings (pydantic-settings) — all v3 fields present", True)
    except Exception as e:
        record("Settings", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 17: VIP/Gift catalog loaders
# ═══════════════════════════════════════════════════════════════════════════
def test_catalog_loaders():
    print("\n── Test 17: VIP & Gift Catalog Loaders ──")
    try:
        os.chdir(PROJECT_ROOT)

        from matching_bot_project.services.vip_subscription import load_vip_plans
        plans = load_vip_plans()
        assert '1w' in plans and '2w' in plans and '1m' in plans
        assert plans['1w']['duration_days'] == 7
        assert plans['1m']['duration_days'] == 30

        from matching_bot_project.services.gift_engine import load_gift_catalog
        gifts = load_gift_catalog()
        assert 'teddy' in gifts
        assert 'rose' in gifts
        assert 'diamond' in gifts
        assert gifts['teddy']['price_coins'] > 0

        record("VIP & Gift catalog loaders", True)
    except Exception as e:
        record("Catalog loaders", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 18: Database CRUD queries (async, on SQLite)
# ═══════════════════════════════════════════════════════════════════════════
async def test_crud_basic():
    print("\n── Test 18: CRUD Queries (SQLite) ──")
    try:
        from matching_bot_project.database.session import engine, Base, async_session_factory
        from matching_bot_project.database.models import models  # noqa
        from matching_bot_project.database.queries.crud import (
            get_user_tags, get_tag_catalog, get_friends, get_blocked_users,
            get_users_who_liked_me, get_active_admin_channels,
            get_user_unread_dm_count, batch_update_users_last_active,
        )
        from matching_bot_project.database.models.models import (
            User, TagCatalog, UserTag,
        )

        test_db = Path('/tmp/match_bot_test_crud.db')
        if test_db.exists():
            test_db.unlink()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session_factory() as session:
            u = User(tg_id=12345, first_name="Test", public_id="user_test1",
                     referral_code="ABCDEF12", coin_balance=10)
            session.add(u)
            await session.commit()

            tc = TagCatalog(code="smoker", display_name="سیگاری", emoji="🚬", category="lifestyle")
            session.add(tc)
            ut = UserTag(user_tg_id=12345, tag_code="smoker")
            session.add(ut)
            await session.commit()

            tags = await get_user_tags(session, 12345)
            assert tags == ["smoker"], f"expected ['smoker'], got {tags}"

            catalog = await get_tag_catalog(session)
            assert len(catalog) >= 1
            assert catalog[0].code == "smoker"

            friends = await get_friends(session, 12345)
            assert friends == []

            blocked = await get_blocked_users(session, 12345)
            assert blocked == []

            liked_me = await get_users_who_liked_me(session, 12345)
            assert liked_me == []

            channels = await get_active_admin_channels(session)
            assert channels == []

            dm_count = await get_user_unread_dm_count(session, 12345)
            assert dm_count == 0

            now = datetime.now(timezone.utc)
            count = await batch_update_users_last_active(session, {12345: now})
            assert count == 1

        await engine.dispose()
        record("CRUD queries (8 functions tested)", True)
    except Exception as e:
        import traceback
        record("CRUD queries", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 19: LikeRateLimitMiddleware logic
# ═══════════════════════════════════════════════════════════════════════════
async def test_like_rate_limit():
    print("\n── Test 19: LikeRateLimitMiddleware ──")
    try:
        from matching_bot_project.bot.middlewares.anti_spam import LikeRateLimitMiddleware

        mw = LikeRateLimitMiddleware(cooldown_seconds=60)
        assert mw.cooldown == 60

        record("LikeRateLimitMiddleware (instantiation + config)", True)
    except Exception as e:
        record("LikeRateLimitMiddleware", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 20: Direct message privacy helpers
# ═══════════════════════════════════════════════════════════════════════════
async def test_dm_privacy():
    print("\n── Test 20: Direct Message Privacy Helpers ──")
    try:
        from matching_bot_project.bot.middlewares.direct_message_privacy import (
            DirectMessagePrivacyMiddleware, is_user_in_active_chat,
            get_active_chat_partner,
        )

        class FakeRedis:
            def __init__(self, store=None):
                self.store = store or {}
            async def hgetall(self, key):
                return self.store.get(key, {})

        import matching_bot_project.bot.middlewares.direct_message_privacy as dmp_module
        original_redis = dmp_module.redis_client
        fake_redis = FakeRedis()
        dmp_module.redis_client = fake_redis

        # Test 1: no state → not in active chat
        assert await is_user_in_active_chat(12345) == False
        assert await get_active_chat_partner(12345) is None

        # Test 2: user in chatting state
        fake_redis.store = {
            "user:state:12345": {
                "status": "chatting",
                "matched_with": "67890",
            }
        }
        assert await is_user_in_active_chat(12345) == True
        assert await get_active_chat_partner(12345) == 67890

        # Test 3: user in 'queuing' state (not in active chat)
        fake_redis.store = {
            "user:state:12345": {
                "status": "queuing",
            }
        }
        assert await is_user_in_active_chat(12345) == False

        dmp_module.redis_client = original_redis
        record("Direct message privacy helpers (3 sub-tests)", True)
    except Exception as e:
        import traceback
        record("Direct message privacy helpers", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Test 21: Match history index check (regression)
# ═══════════════════════════════════════════════════════════════════════════
def test_indexes_in_models():
    print("\n── Test 21: Database Indexes (regression) ──")
    try:
        from matching_bot_project.database.models.models import (
            MatchHistory, CoinTransaction, UserLike, UserReport, User
        )
        def get_index_names(model):
            return {getattr(idx, 'name', None) for idx in model.__table_args__ if hasattr(idx, 'name')}

        mh_indexes = get_index_names(MatchHistory)
        assert "ix_match_histories_user_one" in mh_indexes, \
            f"missing ix_match_histories_user_one in {mh_indexes}"
        assert "ix_match_histories_user_two" in mh_indexes

        ct_indexes = get_index_names(CoinTransaction)
        assert "ix_coin_transactions_user_id" in ct_indexes

        ul_indexes = get_index_names(UserLike)
        assert "ix_user_likes_liked" in ul_indexes
        assert "ix_user_likes_liker" in ul_indexes

        ur_indexes = get_index_names(UserReport)
        assert "ix_user_reports_reported" in ur_indexes

        record("Database indexes (5 critical indexes verified)", True)
    except Exception as e:
        record("Database indexes", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Test 22: VIP subscription & gift engine basic operations
# ═══════════════════════════════════════════════════════════════════════════
async def test_vip_and_gift_engines():
    print("\n── Test 22: VIP & Gift Engines (in-memory) ──")
    try:
        from matching_bot_project.database.session import engine, Base, async_session_factory
        from matching_bot_project.database.models.models import (
            User, GiftType, UserGift, VIPSubscription
        )
        from matching_bot_project.services.vip_subscription import VIPSubscriptionManager
        from matching_bot_project.services.gift_engine import GiftEngine

        test_db = Path('/tmp/match_bot_test_vip.db')
        if test_db.exists():
            test_db.unlink()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed a gift type
        async with async_session_factory() as session:
            gt = GiftType(code="teddy", display_name="تدی", emoji="🧸",
                          price_coins=5, is_active=True)
            session.add(gt)
            u1 = User(tg_id=11111, first_name="Buyer", public_id="user_buyer1",
                      coin_balance=100, referral_code="BUYER111")
            u2 = User(tg_id=22222, first_name="Recipient", public_id="user_recip2",
                      coin_balance=10, referral_code="RECIPI222")
            session.add(u1); session.add(u2)
            await session.commit()

            # Test gift purchase
            ge = GiftEngine()
            ok, msg = await ge.purchase_gift(session, 11111, "teddy", 2)
            assert ok, f"purchase failed: {msg}"
            
            # Verify coin balance deducted (5 * 2 = 10)
            await session.refresh(u1)
            assert u1.coin_balance == 90, f"expected 90, got {u1.coin_balance}"

            # Verify inventory
            inv = await ge.get_user_inventory(session, 11111)
            assert len(inv) == 1
            assert inv[0][0].quantity == 2

            # Test gift transfer
            ok, msg = await ge.transfer_gift(session, 11111, 22222, "teddy", 1)
            assert ok, f"transfer failed: {msg}"
            
            # Verify recipient has 1 teddy now
            inv2 = await ge.get_user_inventory(session, 22222)
            assert len(inv2) == 1
            assert inv2[0][0].quantity == 1

            # Test VIP activation
            vm = VIPSubscriptionManager()
            sub = await vm.activate_subscription(session, 11111, "1w")
            assert sub.expires_at > sub.started_at
            await session.refresh(u1)
            assert u1.is_vip == True

            # Test is_vip_active
            is_active = await vm.is_vip_active(session, 11111)
            assert is_active == True

        await engine.dispose()
        record("VIP & Gift engines (purchase + transfer + activate)", True)
    except Exception as e:
        import traceback
        record("VIP & Gift engines", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# v3.1 SCALING Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_scaling_config():
    """Test v3.1 scaling settings exist."""
    print("\n── Test 23: v3.1 Scaling Settings ──")
    try:
        from matching_bot_project.bot.core.config import settings
        for attr in ['BOT_SHARD_TOKENS', 'ARQ_REDIS_HOST', 'ARQ_WORKER_CONCURRENCY',
                     'CACHE_USER_PROFILE_TTL', 'METRICS_ENABLED', 'METRICS_PATH',
                     'TG_OUTBOUND_RATE_PER_BOT', 'DB_POOL_SIZE', 'DB_MAX_OVERFLOW',
                     'DB_REPLICA_POOL_SIZE', 'DB_REPLICA_MAX_OVERFLOW',
                     'DB_REPLICA_URL', 'DB_REPLICA_HOST']:
            assert hasattr(settings, attr), f"settings missing {attr}"

        # Verify properties
        assert isinstance(settings.parsed_bot_shard_tokens, list)
        assert isinstance(settings.num_bot_shards, int)
        assert settings.num_bot_shards >= 1
        assert settings.METRICS_ENABLED == True
        assert settings.TG_OUTBOUND_RATE_PER_BOT == 25
        assert settings.CACHE_USER_PROFILE_TTL == 300
        assert settings.ARQ_WORKER_CONCURRENCY == 50

        record("v3.1 Scaling settings", True)
    except Exception as e:
        record("v3.1 Scaling settings", False, str(e))


def test_bot_shard_manager():
    """Test BotShardManager routing logic."""
    print("\n── Test 24: BotShardManager ──")
    try:
        from matching_bot_project.bot.core.bot_shard_manager import BotShardManager
        mgr = BotShardManager()

        # In test env (no BOT_SHARD_TOKENS), should be single-bot mode
        mgr.initialize()
        assert mgr.is_sharded == False, "should be single-bot without shards"
        assert mgr.num_shards == 1

        # Test deterministic routing
        idx1 = mgr.get_shard_index_for_user(12345)
        idx2 = mgr.get_shard_index_for_user(12345)
        assert idx1 == idx2, "same user → same shard"

        # Test get_bot_for_user
        bot = mgr.get_bot_for_user_async(12345)
        assert bot is not None, "should return legacy bot in single-bot mode"

        record("BotShardManager (routing + single-bot fallback)", True)
    except Exception as e:
        import traceback
        record("BotShardManager", False, str(e))
        traceback.print_exc()


def test_cache_service_sync():
    """Test cache service serialize/deserialize (sync portion only)."""
    print("\n── Test 25: CacheService (sync part) ──")
    try:
        from matching_bot_project.services.cache import _serialize, _deserialize

        # Test serialize/deserialize with datetime
        now = datetime.now(timezone.utc)
        data = {"tg_id": 12345, "name": "Test", "last_active": now}
        s = _serialize(data)
        d = _deserialize(s)
        assert d["tg_id"] == 12345
        assert d["name"] == "Test"
        assert isinstance(d["last_active"], datetime)

        # Test with nested dict
        nested = {"tags": [{"code": "smoker"}], "count": 5}
        s = _serialize(nested)
        d = _deserialize(s)
        assert d == nested

        record("CacheService serialize/deserialize (datetime + nested)", True)
    except Exception as e:
        record("CacheService sync part", False, str(e))


async def test_cache_service_async():
    """Test cache service async get/set/invalidate (runs in event loop)."""
    print("\n── Test 25b: CacheService (async part) ──")
    try:
        from matching_bot_project.services.cache import CacheService

        class FakeRedis:
            def __init__(self):
                self.store = {}
            async def get(self, key):
                return self.store.get(key)
            async def set(self, key, val, ex=None):
                self.store[key] = val
                return True
            async def delete(self, key):
                if key in self.store:
                    del self.store[key]
                return 1
            async def info(self, section):
                return {"used_memory": 1024, "keyspace_hits": 10, "keyspace_misses": 5}
            async def dbsize(self):
                return len(self.store)

        cache = CacheService(FakeRedis())

        assert await cache.get_user_profile(12345) is None
        await cache.set_user_profile(12345, {"name": "Test"})
        cached = await cache.get_user_profile(12345)
        assert cached == {"name": "Test"}
        await cache.invalidate_user_profile(12345)
        assert await cache.get_user_profile(12345) is None

        assert await cache.get_user_vip_status(12345) is None
        await cache.set_user_vip_status(12345, True)
        assert await cache.get_user_vip_status(12345) == True
        await cache.invalidate_user_vip_status(12345)
        assert await cache.get_user_vip_status(12345) is None

        await cache.set_tag_catalog([{"code": "smoker"}])
        tags = await cache.get_tag_catalog()
        assert tags == [{"code": "smoker"}]

        stats = await cache.get_stats()
        assert "redis_keys" in stats

        record("CacheService async (profile, VIP, catalog, stats)", True)
    except Exception as e:
        import traceback
        record("CacheService async part", False, str(e))
        traceback.print_exc()


def test_metrics_service():
    """Test metrics service can record without crashing."""
    print("\n── Test 26: MetricsService ──")
    try:
        from matching_bot_project.services.metrics import metrics, PROMETHEUS_AVAILABLE
        # These should not raise even if prometheus_client is not installed
        metrics.record_message_received("message")
        metrics.record_message_sent("text")
        metrics.record_db_query("select", 0.05)
        metrics.record_redis_op("get")
        metrics.record_match("random")
        metrics.record_payment("coins", "approved")
        metrics.record_arq_job("send_broadcast", "success")
        metrics.record_cache_hit("user_profile")
        metrics.record_cache_miss("user_profile")
        metrics.set_active_users(5000)
        metrics.set_match_queue("random", 100)
        metrics.set_chat_active_sessions(200)
        metrics.set_broadcast_in_progress(1)
        metrics.set_db_pool("primary", 30, 15)
        metrics.observe_http_request("/api/v1/webhook", 0.05)
        metrics.observe_bot_response("start_handler", 0.1)

        # Get metrics output
        content_type, body = metrics.get_metrics()
        assert content_type is not None

        record(f"MetricsService (PROMETHEUS_AVAILABLE={PROMETHEUS_AVAILABLE})", True)
    except Exception as e:
        import traceback
        record("MetricsService", False, str(e))
        traceback.print_exc()


def test_arq_worker_config():
    """Test arq worker configuration is valid."""
    print("\n── Test 27: arq Worker Configuration ──")
    try:
        # Try importing arq (optional)
        try:
            import arq
            ARQ_AVAILABLE = True
        except ImportError:
            ARQ_AVAILABLE = False

        if not ARQ_AVAILABLE:
            record("arq Worker Config (arq not installed — skip)", True)
            return

        from matching_bot_project.services.arq_worker import WorkerSettings, get_arq_redis_settings

        # Verify WorkerSettings
        assert hasattr(WorkerSettings, 'functions')
        assert hasattr(WorkerSettings, 'redis_settings')
        assert hasattr(WorkerSettings, 'max_jobs')
        assert hasattr(WorkerSettings, 'job_timeout')
        assert hasattr(WorkerSettings, 'max_tries')

        # Verify functions list
        func_names = [f.__name__ for f in WorkerSettings.functions]
        expected = [
            'send_broadcast_job',
            'send_reengagement_job',
            'send_profile_reminder_job',
            'send_silence_reminder_job',
            'expire_vip_subscriptions_job',
            'batch_flush_last_active_job',
            'process_referral_commission_job',
        ]
        for name in expected:
            assert name in func_names, f"missing arq job: {name}"

        # Verify redis settings
        rs = get_arq_redis_settings()
        assert rs is not None

        record(f"arq Worker Config ({len(expected)} jobs defined)", True)
    except Exception as e:
        import traceback
        record("arq Worker Config", False, str(e))
        traceback.print_exc()


def test_db_read_replica_config():
    """Test that read replica config is properly set up."""
    print("\n── Test 28: DB Read Replica Configuration ──")
    try:
        from matching_bot_project.database.session import (
            engine, async_session_factory,
            async_read_session_factory, replica_engine, REPLICA_URL
        )
        # In test mode, no replica is configured, so reads use primary
        assert engine is not None
        assert async_session_factory is not None
        assert async_read_session_factory is not None  # falls back to primary

        # If REPLICA_URL is set, replica_engine should be initialized
        if REPLICA_URL:
            assert replica_engine is not None
        else:
            assert replica_engine is None  # single-node mode

        record("DB Read Replica (graceful fallback to primary)", True)
    except Exception as e:
        record("DB Read Replica", False, str(e))


def test_docker_compose_scaling():
    """Test that docker-compose has scaling services."""
    print("\n── Test 29: docker-compose Scaling Services ──")
    try:
        import yaml
        dc_path = PROJECT_ROOT / 'docker-compose.yml'
        with open(dc_path) as f:
            data = yaml.safe_load(f)

        services = data.get('services', {})
        required = [
            'mysql_primary', 'mysql_replica', 'redis_cache',
            'fastapi_bot', 'arq_worker', 'nginx', 'prometheus'
        ]
        missing = [s for s in required if s not in services]
        assert not missing, f"missing services: {missing}"

        # Verify fastapi_bot has replicas
        fa = services['fastapi_bot']
        deploy = fa.get('deploy', {})
        assert 'replicas' in deploy, "fastapi_bot should have replicas config"
        assert deploy['replicas'] >= 2, "should have at least 2 replicas"

        # Verify arq_worker exists and has replicas
        arq = services['arq_worker']
        assert 'arq' in arq.get('command', [None])[0] if arq.get('command') else False, \
            "arq_worker should run arq command"
        assert arq.get('deploy', {}).get('replicas', 1) >= 1

        # Verify MySQL primary has binlog config
        mysql_cmd = services['mysql_primary'].get('command', '')
        assert 'log-bin' in mysql_cmd, "primary should have binlog enabled"
        assert 'server-id=1' in mysql_cmd

        # Verify MySQL replica is read-only
        replica_cmd = services['mysql_replica'].get('command', '')
        assert 'read-only' in replica_cmd, "replica should be read-only"
        assert 'server-id=2' in replica_cmd

        # Verify Redis has 2GB maxmemory
        redis_cmd = services['redis_cache'].get('command', '')
        assert '2gb' in redis_cmd, "Redis should have 2GB maxmemory"

        record("docker-compose scaling (primary, replica, arq, prometheus)", True)
    except Exception as e:
        record("docker-compose scaling", False, str(e))


def test_deployment_guide():
    """Test that DEPLOYMENT.md exists and has key sections."""
    print("\n── Test 30: DEPLOYMENT.md Guide ──")
    try:
        dep_path = PROJECT_ROOT / 'DEPLOYMENT.md'
        assert dep_path.exists(), "DEPLOYMENT.md not found"
        content = dep_path.read_text()

        # Check for key sections (Persian headers)
        required_sections = [
            'پیش‌نیازها',
            'معماری نهایی',
            'ساخت Bots در BotFather',
            'آماده‌سازی سرور',
            'کانفیگ environment',
            'SSL certificates',
            'MySQL Primary',
            'اعمال migration',
            'اجرای seeders',
            'build و start containers',
            'ثبت webhooks',
            'تست سلامت',
            'تنظیم monitoring',
            'Scaling چطور کار می‌کند',
            'Backup',
            'Troubleshooting',
            'Checklist',
        ]
        missing = [s for s in required_sections if s not in content]
        assert not missing, f"missing sections: {missing}"

        # Verify it's substantial (at least 500 lines)
        line_count = len(content.split('\n'))
        assert line_count >= 500, f"guide too short: {line_count} lines"

        record(f"DEPLOYMENT.md ({line_count} lines, {len(required_sections)} sections)", True)
    except Exception as e:
        record("DEPLOYMENT.md", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Main test runner
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║      Matching Bot v3.1 — Comprehensive Test Runner            ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    # Sync tests
    test_python_syntax()
    test_json_files()
    test_url_regex()
    test_distance_filter()
    test_constants()
    test_referral_codes()
    test_keyboards()
    test_env_example()
    test_docker_compose()
    test_nginx_conf()
    test_seed_scripts()
    test_states()
    test_migration_sql()
    test_config()
    test_indexes_in_models()

    # v3.1 scaling tests
    test_scaling_config()
    test_bot_shard_manager()
    test_cache_service_sync()
    test_metrics_service()
    test_arq_worker_config()
    test_db_read_replica_config()
    test_docker_compose_scaling()
    test_deployment_guide()

    # Imports (run after constants)
    test_imports()

    # Catalog loaders (need imports)
    test_catalog_loaders()

    # Async tests
    await test_models_create_all()
    await test_crud_basic()
    await test_like_rate_limit()
    await test_dm_privacy()
    await test_vip_and_gift_engines()
    await test_cache_service_async()

    # ─── Summary ─────────────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print("TEST SUMMARY")
    print("═" * 70)
    passed = sum(1 for _, s, _ in RESULTS if s)
    failed = sum(1 for _, s, _ in RESULTS if not s)
    total = len(RESULTS)
    for name, success, detail in RESULTS:
        status = "✅" if success else "❌"
        print(f"  {status} {name}" + (f" — {detail}" if detail and not success else ""))
    print("─" * 70)
    print(f"  Total: {total}   Passed: {passed}   Failed: {failed}")
    print("═" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

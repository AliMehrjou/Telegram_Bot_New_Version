"""
services/metrics.py

v3.1 SCALING: Prometheus metrics for production observability.

Exposes /metrics endpoint via FastAPI for Prometheus scraping.

Metrics tracked:
- bot_messages_received_total{type} — counter
- bot_messages_sent_total{type} — counter
- bot_active_users — gauge (real-time)
- db_query_duration_seconds{operation} — histogram
- db_pool_size{engine} — gauge
- redis_operations_total{operation} — counter
- match_queue_size{type} — gauge
- chat_active_sessions — gauge
- broadcast_in_progress — gauge
- arq_jobs_queued — gauge
- arq_jobs_completed_total{status} — counter
- http_request_duration_seconds{endpoint} — histogram
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import prometheus_client; if not available, metrics are no-ops.
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not installed — metrics disabled.")

# Custom registry to avoid conflicts
REGISTRY = CollectorRegistry() if PROMETHEUS_AVAILABLE else None

if PROMETHEUS_AVAILABLE:
    # ─── Counters ────────────────────────────────────────────────────────────
    messages_received = Counter(
        "bot_messages_received_total",
        "Total messages received from Telegram",
        ["type"],  # message, callback_query, my_chat_member
        registry=REGISTRY,
    )
    messages_sent = Counter(
        "bot_messages_sent_total",
        "Total messages sent to Telegram",
        ["type"],  # text, photo, video, voice, copy
        registry=REGISTRY,
    )
    db_queries = Counter(
        "db_queries_total",
        "Total DB queries executed",
        ["operation"],  # select, insert, update, delete
        registry=REGISTRY,
    )
    redis_ops = Counter(
        "redis_operations_total",
        "Total Redis operations",
        ["operation"],  # get, set, hget, hset, lpush, etc.
        registry=REGISTRY,
    )
    matches_made = Counter(
        "bot_matches_made_total",
        "Total matches made",
        ["match_type"],  # random, boy, girl, nearby, same_age
        registry=REGISTRY,
    )
    payments_processed = Counter(
        "bot_payments_processed_total",
        "Total payments processed",
        ["type", "status"],  # type: coins/vip/gift, status: approved/rejected/failed
        registry=REGISTRY,
    )
    arq_jobs = Counter(
        "arq_jobs_total",
        "Total arq jobs processed",
        ["job_name", "status"],  # status: success, failure
        registry=REGISTRY,
    )
    cache_hits = Counter(
        "cache_hits_total",
        "Cache hits",
        ["cache_name"],
        registry=REGISTRY,
    )
    cache_misses = Counter(
        "cache_misses_total",
        "Cache misses",
        ["cache_name"],
        registry=REGISTRY,
    )

    # ─── Gauges ──────────────────────────────────────────────────────────────
    active_users = Gauge(
        "bot_active_users",
        "Currently active users (last 5 min)",
        registry=REGISTRY,
    )
    match_queue_size = Gauge(
        "bot_match_queue_size",
        "Users waiting in match queue",
        ["queue_type"],
        registry=REGISTRY,
    )
    chat_active_sessions = Gauge(
        "bot_chat_active_sessions",
        "Active chat/date sessions",
        registry=REGISTRY,
    )
    broadcast_in_progress = Gauge(
        "bot_broadcast_in_progress",
        "Broadcasts currently running",
        registry=REGISTRY,
    )
    db_pool_size = Gauge(
        "db_pool_size",
        "DB connection pool size",
        ["engine"],  # primary, replica
        registry=REGISTRY,
    )
    db_pool_checked_out = Gauge(
        "db_pool_checked_out",
        "DB connections currently checked out",
        ["engine"],
        registry=REGISTRY,
    )
    arq_jobs_queued = Gauge(
        "arq_jobs_queued",
        "arq jobs waiting in queue",
        registry=REGISTRY,
    )

    # ─── Histograms ──────────────────────────────────────────────────────────
    db_query_duration = Histogram(
        "db_query_duration_seconds",
        "DB query duration in seconds",
        ["operation"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        registry=REGISTRY,
    )
    http_request_duration = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["endpoint"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
        registry=REGISTRY,
    )
    bot_response_duration = Histogram(
        "bot_response_duration_seconds",
        "Bot response time (from update received to handler completed)",
        ["handler"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        registry=REGISTRY,
    )


class MetricsService:
    """Wrapper for prometheus metrics. No-op if prometheus_client not installed."""

    @staticmethod
    def record_message_received(msg_type: str = "message") -> None:
        if PROMETHEUS_AVAILABLE:
            messages_received.labels(type=msg_type).inc()

    @staticmethod
    def record_message_sent(msg_type: str = "text") -> None:
        if PROMETHEUS_AVAILABLE:
            messages_sent.labels(type=msg_type).inc()

    @staticmethod
    def record_db_query(operation: str, duration_sec: float) -> None:
        if PROMETHEUS_AVAILABLE:
            db_queries.labels(operation=operation).inc()
            db_query_duration.labels(operation=operation).observe(duration_sec)

    @staticmethod
    def record_redis_op(operation: str) -> None:
        if PROMETHEUS_AVAILABLE:
            redis_ops.labels(operation=operation).inc()

    @staticmethod
    def record_match(match_type: str) -> None:
        if PROMETHEUS_AVAILABLE:
            matches_made.labels(match_type=match_type).inc()

    @staticmethod
    def record_payment(pay_type: str, status: str) -> None:
        if PROMETHEUS_AVAILABLE:
            payments_processed.labels(type=pay_type, status=status).inc()

    @staticmethod
    def record_arq_job(job_name: str, status: str) -> None:
        if PROMETHEUS_AVAILABLE:
            arq_jobs.labels(job_name=job_name, status=status).inc()

    @staticmethod
    def record_cache_hit(cache_name: str) -> None:
        if PROMETHEUS_AVAILABLE:
            cache_hits.labels(cache_name=cache_name).inc()

    @staticmethod
    def record_cache_miss(cache_name: str) -> None:
        if PROMETHEUS_AVAILABLE:
            cache_misses.labels(cache_name=cache_name).inc()

    @staticmethod
    def set_active_users(count: int) -> None:
        if PROMETHEUS_AVAILABLE:
            active_users.set(count)

    @staticmethod
    def set_match_queue(queue_type: str, size: int) -> None:
        if PROMETHEUS_AVAILABLE:
            match_queue_size.labels(queue_type=queue_type).set(size)

    @staticmethod
    def set_chat_active_sessions(count: int) -> None:
        if PROMETHEUS_AVAILABLE:
            chat_active_sessions.set(count)

    @staticmethod
    def set_broadcast_in_progress(count: int) -> None:
        if PROMETHEUS_AVAILABLE:
            broadcast_in_progress.set(count)

    @staticmethod
    def set_db_pool(engine: str, size: int, checked_out: int) -> None:
        if PROMETHEUS_AVAILABLE:
            db_pool_size.labels(engine=engine).set(size)
            db_pool_checked_out.labels(engine=engine).set(checked_out)

    @staticmethod
    def observe_http_request(endpoint: str, duration_sec: float) -> None:
        if PROMETHEUS_AVAILABLE:
            http_request_duration.labels(endpoint=endpoint).observe(duration_sec)

    @staticmethod
    def observe_bot_response(handler: str, duration_sec: float) -> None:
        if PROMETHEUS_AVAILABLE:
            bot_response_duration.labels(handler=handler).observe(duration_sec)

    @staticmethod
    def get_metrics() -> tuple[str, str]:
        """Return (content_type, body) for /metrics endpoint."""
        if not PROMETHEUS_AVAILABLE:
            return "text/plain", "prometheus_client not installed"
        return CONTENT_TYPE_LATEST, generate_latest(REGISTRY)


# Singleton
metrics = MetricsService()

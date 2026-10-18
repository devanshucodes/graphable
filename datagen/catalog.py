"""Hand-designed static catalog for the fictional company: services, datastores, teams,
topology, endpoint vocabulary, failure mechanisms, alert names and writing personas."""

# name: (domain, tier, language, entry_point, customer_facing, aliases)
SERVICES = {
    # Edge
    "api-gateway": ("edge", 0, "Go", True, True, ["gateway", "API GW", "edge gateway"]),
    "web-bff": ("edge", 0, "Node", False, True, ["web BFF", "webbff", "web backend"]),
    "mobile-bff": ("edge", 0, "Node", False, True, ["mobile BFF", "app backend"]),
    "partner-api": ("edge", 1, "Java", True, True, ["partner API", "B2B API"]),
    # Identity
    "auth": ("identity", 0, "Go", False, True, ["auth-svc", "authn", "login service"]),
    "user-profile": ("identity", 1, "Java", False, True, ["profile service", "user svc"]),
    "session": ("identity", 0, "Go", False, True, ["session-svc", "sessions"]),
    "permissions": ("identity", 1, "Go", False, False, ["perms", "authz", "permissions service"]),
    # Catalog
    "catalog": ("catalog", 1, "Java", False, True, ["catalog-svc", "product catalog"]),
    "search": ("catalog", 1, "Python", False, True, ["search-svc", "product search"]),
    "recommendations": ("catalog", 2, "Python", False, True, ["recs", "reco", "recommender"]),
    "pricing": ("catalog", 1, "Kotlin", False, True, ["pricing-svc", "price engine"]),
    "inventory": ("catalog", 1, "Java", False, True, ["inventory-svc", "stock service"]),
    "reviews": ("catalog", 3, "Node", False, True, ["reviews-svc", "ratings"]),
    # Checkout
    "cart": ("checkout", 0, "Go", False, True, ["cart-svc", "basket"]),
    "checkout": ("checkout", 0, "Java", False, True, ["checkout-svc", "checkout API", "co-svc"]),
    "promotions": ("checkout", 1, "Kotlin", False, True, ["promos", "promo engine", "coupons"]),
    "tax": ("checkout", 1, "Java", False, False, ["tax-svc", "tax engine"]),
    "address-validation": ("checkout", 2, "Python", False, False, ["addr-val", "address svc"]),
    # Payments
    "payments": ("payments", 0, "Java", False, True, ["pay-svc", "Payments API", "payment service"]),
    "fraud-detection": ("payments", 1, "Python", False, False, ["fraud", "risk engine", "fraud-svc"]),
    "ledger": ("payments", 0, "Java", False, False, ["ledger-svc", "the ledger"]),
    "payouts": ("payments", 1, "Java", False, False, ["payouts-svc", "seller payouts"]),
    "refunds": ("payments", 1, "Java", False, True, ["refunds-svc", "refund service"]),
    "billing": ("payments", 1, "Kotlin", False, False, ["billing-svc", "invoicing"]),
    # Fulfillment
    "orders": ("fulfillment", 0, "Java", False, True, ["orders-svc", "order service", "OMS"]),
    "shipping": ("fulfillment", 1, "Go", False, True, ["shipping-svc", "shipments"]),
    "warehouse": ("fulfillment", 2, "Java", False, False, ["wms", "warehouse-svc"]),
    "returns": ("fulfillment", 2, "Node", False, True, ["returns-svc", "RMA service"]),
    "notifications": ("fulfillment", 1, "Node", False, True, ["notif", "notify-svc", "notifier"]),
    # Data & ML
    "event-ingest": ("data", 1, "Go", False, False, ["ingest", "event pipeline"]),
    "feature-store": ("data", 1, "Python", False, False, ["features", "fstore"]),
    "analytics": ("data", 3, "Python", False, False, ["analytics-svc", "BI pipeline"]),
    "ml-scoring": ("data", 1, "Python", False, False, ["scoring", "model serving"]),
    # Platform
    "kafka": ("platform", 0, "Java", False, False, ["the bus", "kafka cluster", "event bus"]),
    "service-mesh": ("platform", 0, "Go", False, False, ["mesh", "istio", "sidecars"]),
    "config-service": ("platform", 0, "Go", False, False, ["config svc", "configsvc", "dynamic config"]),
    "feature-flags": ("platform", 0, "Go", False, False, ["flags", "flag service", "FF svc"]),
    "vault": ("platform", 0, "Go", False, False, ["secrets", "vault cluster"]),
    "scheduler": ("platform", 2, "Go", False, False, ["cron svc", "job scheduler"]),
}

# name: (engine, tier, owner team key)
DATASTORES = {
    "pg-users": ("postgres", 0, "identity"), "pg-catalog": ("postgres", 1, "discovery"),
    "pg-inventory": ("postgres", 1, "pricing_inventory"), "pg-orders": ("postgres", 0, "fulfillment"),
    "pg-payments": ("postgres", 0, "payments"), "pg-ledger": ("postgres", 0, "money"),
    "pg-billing": ("postgres", 1, "money"), "redis-cache": ("redis", 0, "platform"),
    "redis-session": ("redis", 0, "identity"), "redis-ratelimit": ("redis", 1, "edge"),
    "es-search": ("elasticsearch", 1, "discovery"), "cassandra-events": ("cassandra", 1, "data"),
    "clickhouse-analytics": ("clickhouse", 3, "data"), "s3-assets": ("object-store", 2, "platform"),
    "ext-paylynx": ("external-psp", 0, "payments"),
}

# team key: (display name, initial services, nominal size)
TEAMS = {
    "edge": ("Edge", ["api-gateway", "web-bff", "mobile-bff", "partner-api"], 10),
    "identity": ("Identity", ["auth", "user-profile", "session", "permissions"], 9),
    "discovery": ("Discovery", ["catalog", "search", "recommendations", "reviews"], 14),
    "pricing_inventory": ("Pricing & Inventory", ["pricing", "inventory"], 9),
    "checkout": ("Checkout", ["cart", "checkout", "promotions", "tax", "address-validation"], 11),
    "payments": ("Payments", ["payments", "fraud-detection", "refunds"], 12),
    "money": ("Money", ["ledger", "payouts", "billing"], 8),
    "fulfillment": ("Fulfillment", ["orders", "shipping", "warehouse", "returns", "notifications"], 12),
    "data": ("Data & ML", ["event-ingest", "feature-store", "analytics", "ml-scoring"], 11),
    "platform": ("Platform/SRE", ["kafka", "service-mesh", "config-service", "feature-flags", "vault", "scheduler"], 14),
    "orbit": ("Orbit", [], 6),  # created 2026-07-01
}

# Explicit dependencies: src -> [(dst, kind, critical)]
_D = lambda *xs: list(xs)
DEPENDENCIES = {
    "api-gateway": _D(("web-bff", "sync", True), ("mobile-bff", "sync", True), ("partner-api", "sync", False),
                      ("auth", "sync", True), ("redis-ratelimit", "cache", False)),
    "web-bff": _D(("catalog", "sync", True), ("search", "sync", True), ("cart", "sync", True),
                  ("checkout", "sync", True), ("user-profile", "sync", False), ("recommendations", "sync", False),
                  ("orders", "sync", False), ("session", "sync", True), ("reviews", "sync", False)),
    "mobile-bff": _D(("catalog", "sync", True), ("search", "sync", True), ("cart", "sync", True),
                     ("checkout", "sync", True), ("user-profile", "sync", False), ("orders", "sync", False),
                     ("session", "sync", True), ("notifications", "async", False)),
    "partner-api": _D(("catalog", "sync", True), ("inventory", "sync", True), ("orders", "sync", True),
                      ("pricing", "sync", True), ("auth", "sync", True), ("permissions", "sync", True)),
    "auth": _D(("pg-users", "db", True), ("redis-session", "cache", True), ("vault", "sync", True),
               ("permissions", "sync", True)),
    "user-profile": _D(("pg-users", "db", True), ("redis-cache", "cache", False), ("kafka", "queue", False)),
    "session": _D(("redis-session", "cache", True), ("auth", "sync", True)),
    "permissions": _D(("pg-users", "db", True), ("redis-cache", "cache", False)),
    "catalog": _D(("pg-catalog", "db", True), ("redis-cache", "cache", True), ("kafka", "queue", False),
                  ("s3-assets", "db", False)),
    "search": _D(("es-search", "db", True), ("pg-catalog", "db", True), ("catalog", "sync", False),
                 ("redis-cache", "cache", False)),
    "recommendations": _D(("feature-store", "sync", True), ("ml-scoring", "sync", True), ("catalog", "sync", False),
                          ("redis-cache", "cache", False)),
    "pricing": _D(("pg-catalog", "db", True), ("promotions", "sync", False), ("redis-cache", "cache", True),
                  ("kafka", "queue", False)),
    "inventory": _D(("pg-inventory", "db", True), ("kafka", "queue", True), ("orders", "async", False)),
    "reviews": _D(("pg-catalog", "db", True), ("user-profile", "sync", False), ("kafka", "queue", False)),
    "cart": _D(("redis-cache", "cache", True), ("pricing", "sync", True), ("inventory", "sync", True),
               ("promotions", "sync", False), ("session", "sync", True)),
    "checkout": _D(("cart", "sync", True), ("payments", "sync", True), ("tax", "sync", True),
                   ("address-validation", "sync", False), ("inventory", "sync", True), ("orders", "sync", True),
                   ("promotions", "sync", False), ("auth", "sync", True)),
    "promotions": _D(("pg-catalog", "db", True), ("redis-cache", "cache", True), ("feature-flags", "sync", False)),
    "tax": _D(("address-validation", "sync", True), ("redis-cache", "cache", False)),
    "address-validation": _D(("redis-cache", "cache", False),),
    "payments": _D(("pg-payments", "db", True), ("fraud-detection", "sync", True), ("ledger", "sync", True),
                   ("vault", "sync", True), ("kafka", "queue", False), ("redis-cache", "cache", True),
                   ("ext-paylynx", "sync", True)),
    "fraud-detection": _D(("ml-scoring", "sync", True), ("feature-store", "sync", True),
                          ("pg-payments", "db", False), ("redis-cache", "cache", False)),
    "ledger": _D(("pg-ledger", "db", True), ("kafka", "queue", True)),
    "payouts": _D(("ledger", "sync", True), ("pg-ledger", "db", True), ("vault", "sync", True),
                  ("scheduler", "sync", False), ("ext-paylynx", "sync", True)),
    "refunds": _D(("payments", "sync", True), ("ledger", "sync", True), ("orders", "sync", False),
                  ("kafka", "queue", False)),
    "billing": _D(("pg-billing", "db", True), ("ledger", "sync", True), ("scheduler", "sync", False),
                  ("notifications", "async", False)),
    "orders": _D(("pg-orders", "db", True), ("kafka", "queue", True), ("inventory", "async", False),
                 ("payments", "sync", True), ("notifications", "async", False)),
    "shipping": _D(("orders", "sync", True), ("address-validation", "sync", False), ("kafka", "queue", False),
                   ("pg-orders", "db", False)),
    "warehouse": _D(("inventory", "sync", True), ("kafka", "queue", True), ("pg-inventory", "db", False)),
    "returns": _D(("orders", "sync", True), ("refunds", "sync", True), ("shipping", "sync", False),
                  ("pg-orders", "db", False)),
    "notifications": _D(("kafka", "queue", True), ("user-profile", "sync", False), ("redis-cache", "cache", False)),
    "event-ingest": _D(("kafka", "queue", True), ("cassandra-events", "db", True)),
    "feature-store": _D(("cassandra-events", "db", True), ("redis-cache", "cache", True)),
    "analytics": _D(("clickhouse-analytics", "db", True), ("kafka", "queue", False), ("event-ingest", "async", False)),
    "ml-scoring": _D(("feature-store", "sync", True), ("s3-assets", "db", False)),
    "feature-flags": _D(("redis-cache", "cache", False), ("config-service", "sync", True)),
    "scheduler": _D(("kafka", "queue", False),),
}
PLATFORM = {"kafka", "service-mesh", "config-service", "feature-flags", "vault", "scheduler"}
FLAG_CONSUMERS = {"web-bff", "mobile-bff", "checkout", "cart", "search", "recommendations", "pricing",
                  "payments", "orders", "notifications", "promotions", "partner-api", "catalog"}

# Topology changes over the year, each tied to a decision (bi-temporal edges)
# (date as (y,m,d), op, src, dst, kind, critical, decision title)
TOPOLOGY_CHANGES = [
    ((2025, 11, 12), "add", "orders", "fraud-detection", "sync", False, "Screen high-risk orders before fulfillment"),
    ((2026, 1, 20), "add", "recommendations", "kafka", "queue", False, "Stream click events into recommendations"),
    ((2026, 2, 10), "remove", "search", "pg-catalog", "db", True, "Move search reads off pg-catalog onto es-search"),
    ((2026, 3, 3), "add", "checkout", "fraud-detection", "sync", False, "Inline fraud pre-check in checkout"),
    ((2026, 5, 18), "remove", "reviews", "user-profile", "sync", False, "Denormalize reviewer names into reviews"),
    ((2026, 6, 9), "add", "notifications", "feature-flags", "sync", False, "Gate notification channels behind flags"),
    ((2026, 8, 4), "add", "shipping", "redis-cache", "cache", False, "Cache carrier rate quotes"),
    ((2026, 8, 25), "remove", "pricing", "promotions", "sync", False, "Pre-compute promo prices asynchronously"),
    ((2026, 9, 8), "add", "payouts", "kafka", "queue", False, "Emit payout events for reconciliation"),
]

ENDPOINT_NOUNS = {
    "api-gateway": ["route", "health", "ratelimit"], "web-bff": ["home", "pdp", "plp", "account", "cart-view"],
    "mobile-bff": ["feed", "product", "cart", "account", "push-register"], "partner-api": ["catalog", "orders", "stock", "prices"],
    "auth": ["token", "login", "logout", "refresh", "mfa"], "user-profile": ["profile", "addresses", "preferences"],
    "session": ["session", "heartbeat"], "permissions": ["check", "roles", "grants"],
    "catalog": ["products", "categories", "brands", "media"], "search": ["query", "suggest", "facets", "reindex"],
    "recommendations": ["for-you", "similar", "bought-together"], "pricing": ["quote", "rules", "currency"],
    "inventory": ["stock", "reserve", "release", "levels"], "reviews": ["reviews", "ratings", "moderation"],
    "cart": ["cart", "items", "merge"], "checkout": ["session", "place-order", "summary", "confirm"],
    "promotions": ["apply", "validate", "campaigns"], "tax": ["calculate", "rates"],
    "address-validation": ["validate", "autocomplete"], "payments": ["authorize", "capture", "void", "methods"],
    "fraud-detection": ["score", "rules", "review-queue"], "ledger": ["entries", "balances", "reconcile"],
    "payouts": ["payouts", "schedule", "status"], "refunds": ["refund", "status"],
    "billing": ["invoices", "subscriptions", "usage"], "orders": ["orders", "status", "cancel", "history"],
    "shipping": ["quote", "label", "track"], "warehouse": ["pick", "pack", "dispatch"],
    "returns": ["rma", "label", "status"], "notifications": ["send", "templates", "preferences"],
    "event-ingest": ["events", "batch"], "feature-store": ["features", "online", "backfill"],
    "analytics": ["reports", "exports"], "ml-scoring": ["predict", "models"],
    "kafka": ["admin"], "service-mesh": ["admin"], "config-service": ["config", "watch", "history"],
    "feature-flags": ["flags", "evaluate", "rollout"], "vault": ["secrets", "pki", "leases"],
    "scheduler": ["jobs", "runs"],
}

# Failure mechanisms by change kind: (mechanism, symptom, alerts, fix, runbook topic)
MECHANISMS = {
    "config": [
        ("connection pool max lowered", "latency", ["LatencyP99Breach", "ConnPoolExhausted"], "rollback", "connection-pool"),
        ("timeout lowered below dependency p99", "errors", ["5xxSpike", "UpstreamTimeouts"], "rollback", "timeouts"),
        ("cache eviction policy changed", "latency", ["LatencyP99Breach", "CacheHitRateDrop"], "rollback", "cache-eviction"),
        ("rate limit threshold lowered", "errors", ["429Spike", "HighErrorRate"], "rollback", "rate-limits"),
        ("retry policy made aggressive", "saturation", ["SaturationCPU", "RetryStorm"], "rollback", "retry-storm"),
        ("TLS cipher list tightened", "errors", ["TLSHandshakeErrors", "5xxSpike"], "rollback", "tls"),
    ],
    "code": [
        ("N+1 query regression", "latency", ["LatencyP99Breach", "DBCPUHigh"], "rollback", "slow-queries"),
        ("memory leak in request handler", "saturation", ["MemoryPressure", "PodRestarts"], "rollback", "memory-leak"),
        ("null handling bug on new field", "errors", ["HighErrorRate", "5xxSpike"], "rollback", "error-spike"),
        ("serialization format change", "errors", ["DeserializationErrors", "HighErrorRate"], "rollback", "schema-compat"),
        ("thread pool exhaustion under load", "latency", ["ThreadPoolSaturated", "LatencyP99Breach"], "rollback", "thread-pools"),
    ],
    "flag": [
        ("flag enabled untested code path", "errors", ["HighErrorRate", "5xxSpike"], "flag_off", "flag-rollback"),
        ("flag rollout jumped to 100%", "saturation", ["SaturationCPU", "LatencyP99Breach"], "flag_off", "flag-rollback"),
    ],
    "migration": [
        ("lock-taking migration on hot table", "latency", ["DBLockWaits", "LatencyP99Breach"], "kill_migration", "db-locks"),
        ("dropped index still in use", "latency", ["DBCPUHigh", "SlowQueries"], "restore_index", "slow-queries"),
    ],
    "dependency_bump": [
        ("client library bump changed default timeout", "errors", ["UpstreamTimeouts", "HighErrorRate"], "rollback", "timeouts"),
        ("driver bump broke connection reuse", "saturation", ["ConnPoolExhausted", "SaturationCPU"], "rollback", "connection-pool"),
    ],
    "cert": [
        ("rotation missed intermediate certificate", "errors", ["TLSHandshakeErrors", "CertChainInvalid"], "reissue_cert", "tls"),
    ],
}
NON_CHANGE = {
    "capacity": ("traffic surge beyond capacity", "saturation", ["SaturationCPU", "LatencyP99Breach", "QueueLag"], "scale_out", "capacity"),
    "cert_expiry": ("TLS certificate expired", "errors", ["CertExpired", "TLSHandshakeErrors"], "renew_cert", "cert-renewal"),
    "external_outage": ("PayLynx processor outage", "errors", ["UpstreamErrors", "HighErrorRate"], "failover_vendor", "vendor-outage"),
    "node_failure": ("node pool failure in one zone", "errors", ["PodRestarts", "HighErrorRate"], "failover_zone", "zone-failover"),
    "data_issue": ("bad backfill corrupted records", "errors", ["DataValidationFailed", "HighErrorRate"], "data_fix", "data-repair"),
    "flag_drift": ("stale flag targeting rule", "errors", ["HighErrorRate", "5xxSpike"], "flag_off", "flag-rollback"),
}
SYMPTOM_ALERTS = {
    "latency": ["LatencyP99Breach", "UpstreamTimeouts", "SlowResponses"],
    "errors": ["HighErrorRate", "5xxSpike", "UpstreamErrors"],
    "saturation": ["SaturationCPU", "QueueLag", "LatencyP99Breach"],
}
NOISE_ALERTS = ["DiskUsageHigh", "PodRestarts", "LatencyP99Breach", "HighErrorRate", "QueueLag",
                "GCPauseLong", "CertExpirySoon", "SaturationCPU", "ProbeFlapping", "5xxSpike"]

CHANGE_SUMMARIES = {
    "code": ["release {v}: {feat}", "hotfix {v}: {feat}", "deploy {v} ({feat})"],
    "config": ["{key}: {old} -> {new}"],
    "flag": ["flag {flag} rollout {old}% -> {new}%", "flag {flag} enabled for {seg}"],
    "migration": ["migration {mid}: {what}"],
    "dependency_bump": ["bump {lib} {old} -> {new}"],
    "cert": ["rotate TLS cert for {host}"],
}
FEATURES = ["new tax rounding", "faster cart merge", "bulk price import", "async receipts", "guest checkout v2",
            "address autocomplete", "saved cards", "split shipments", "promo stacking", "search synonyms",
            "image CDN switch", "currency rounding fix", "retry jitter", "pagination cleanup", "audit logging",
            "fraud rule tuning", "payout batching", "refund webhooks", "inventory holds", "session refresh"]
CONFIG_KEYS = {
    "connection pool max lowered": ("db.pool.max", "64", "16"),
    "timeout lowered below dependency p99": ("http.client.timeout_ms", "2000", "250"),
    "cache eviction policy changed": ("maxmemory-policy", "allkeys-lru", "volatile-lru"),
    "rate limit threshold lowered": ("ratelimit.rps", "500", "120"),
    "retry policy made aggressive": ("retry.max_attempts", "2", "6"),
    "TLS cipher list tightened": ("tls.ciphers", "default", "modern-only"),
}
BENIGN_CONFIG = [("log.level", "info", "debug"), ("feature.banner", "off", "on"), ("cache.ttl_s", "300", "600"),
                 ("http.keepalive_s", "60", "90"), ("batch.size", "100", "200"), ("metrics.sample_rate", "0.1", "0.2")]
LIBS = ["grpc-java", "netty", "pgx", "lettuce", "kafka-clients", "jackson", "okhttp", "requests", "express", "ktor"]

PERSONAS = ["terse SRE", "verbose architect", "friendly emoji-heavy PM", "careful senior engineer",
            "junior eager engineer", "dry humor staff engineer", "formal manager", "data scientist"]
ROLES_MIX = {"backend": 0.55, "frontend": 0.08, "sre": 0.12, "data": 0.08, "em": 0.08, "pm": 0.06, "security": 0.03}
SENIORITY = [("junior", 0.2), ("mid", 0.35), ("senior", 0.3), ("staff", 0.12), ("principal", 0.03)]
TIMEZONES = ["Europe/Berlin", "Europe/London", "America/New_York", "Asia/Kolkata", "Europe/Stockholm"]

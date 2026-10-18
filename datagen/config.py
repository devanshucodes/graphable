"""Generator configuration: seed, time windows, scale targets."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MIN_MS = 60_000


def ms(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


def iso(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Config:
    tenant: str = "acme"
    company: str = "Acme Commerce"
    email_domain: str = "acme-fake.dev"
    seed: int = 20261015
    history_start: int = ms(2025, 10, 1)
    history_end: int = ms(2025, 10, 1) + 365 * DAY_MS - 1  # through 2026-09-30
    test_start: int = ms(2026, 10, 1)
    test_end: int = ms(2026, 10, 14, 23, 59)
    # calendar events
    sale_start: int = ms(2025, 11, 27)
    sale_end: int = ms(2025, 12, 1, 23, 59)
    freeze_start: int = ms(2025, 12, 18)
    freeze_end: int = ms(2026, 1, 5, 23, 59)
    merge_money_at: int = ms(2026, 4, 15, 9)
    orbit_created_at: int = ms(2026, 7, 1, 9)
    vault_expert_leaves_at: int = ms(2026, 6, 20, 17)
    # scale
    services: list[str] | None = None          # None = full catalog
    people_scale: float = 1.0
    change_rate_scale: float = 1.0
    bad_change_base: float = 0.0105
    non_change_incidents: dict = field(default_factory=lambda: {
        "capacity": 45, "cert_expiry": 20, "external_outage": 25,
        "node_failure": 25, "data_issue": 20, "flag_drift": 15,
    })
    noise_alerts: int = 3200
    help_threads: int = 800
    chatter_threads: int = 840
    bug_tickets: int = 1200
    task_tickets: int = 1000
    ops_tickets: int = 200
    wiki_howtos: int = 150
    design_docs: int = 100
    decisions: int = 120
    departures: int = 8
    joiners: int = 14
    n_test_scenarios: int = 40
    full_org_events: bool = True


def globex_config() -> Config:
    """Small second tenant with identical service names (isolation test)."""
    return Config(
        tenant="globex", company="Globex Retail", email_domain="globex-fake.dev", seed=7331,
        history_start=ms(2026, 8, 1), history_end=ms(2026, 9, 30, 23, 59),
        services=["api-gateway", "web-bff", "auth", "session", "catalog", "search", "cart",
                  "checkout", "payments", "orders", "notifications", "kafka", "config-service",
                  "vault", "service-mesh"],
        people_scale=0.17, change_rate_scale=1.0,
        non_change_incidents={"capacity": 2, "cert_expiry": 1, "external_outage": 1,
                              "node_failure": 2, "data_issue": 1, "flag_drift": 1},
        noise_alerts=150, help_threads=25, chatter_threads=30, bug_tickets=40,
        task_tickets=30, ops_tickets=8, wiki_howtos=8, design_docs=4, decisions=4,
        departures=0, joiners=1, n_test_scenarios=0, full_org_events=False,
    )

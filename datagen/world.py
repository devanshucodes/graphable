"""World builder: services, datastores, topology (bi-temporal), org + history, on-call,
endpoints, flags, and the hidden expertise/fragility model."""
from __future__ import annotations

import random
import re
from collections import defaultdict
from dataclasses import dataclass, field

from faker import Faker

from . import catalog as C
from .config import DAY_MS, HOUR_MS, Config, ms

SENIORITY_BASE = {"junior": 0.3, "mid": 0.5, "senior": 0.7, "staff": 0.85, "principal": 0.9}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


@dataclass
class World:
    cfg: Config
    rng: random.Random
    services: dict = field(default_factory=dict)      # id -> record
    datastores: dict = field(default_factory=dict)
    regions: list = field(default_factory=list)
    endpoints: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    teams: dict = field(default_factory=dict)
    people: dict = field(default_factory=dict)
    memberships: list = field(default_factory=list)   # bi-temporal MEMBER_OF
    ownership: list = field(default_factory=list)     # bi-temporal OWNS
    reporting: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)  # bi-temporal DEPENDS_ON
    oncall: list = field(default_factory=list)
    org_events: list = field(default_factory=list)
    expertise: dict = field(default_factory=dict)     # truth: person -> entity -> 0..1
    fragility: dict = field(default_factory=dict)     # truth
    review_rigor: dict = field(default_factory=dict)  # truth
    special: dict = field(default_factory=dict)       # named people for hero scenarios

    # ---------- temporal lookups ----------
    def active(self, rec: dict, t: int) -> bool:
        return rec["valid_from"] <= t and (rec["valid_to"] is None or t < rec["valid_to"])

    def owner(self, entity_id: str, t: int) -> str | None:
        for o in self.ownership:
            if o["entity_id"] == entity_id and self.active(o, t):
                return o["team_id"]
        return None

    def team_of(self, person_id: str, t: int) -> str | None:
        for m in self._mem_by_person.get(person_id, []):
            if self.active(m, t):
                return m["team_id"]
        return None

    def members(self, team_id: str, t: int, roles: set | None = None) -> list[str]:
        out = []
        for m in self._mem_by_team.get(team_id, []):
            if self.active(m, t) and self.person_active(m["person_id"], t):
                if roles is None or self.people[m["person_id"]]["role"] in roles:
                    out.append(m["person_id"])
        return out

    def person_active(self, pid: str, t: int) -> bool:
        p = self.people[pid]
        return p["joined_at"] <= t and (p["left_at"] is None or t < p["left_at"])

    def deps_at(self, t: int) -> tuple[dict, dict]:
        fwd, rev = defaultdict(list), defaultdict(list)
        for d in self.dependencies:
            if self.active(d, t):
                fwd[d["src"]].append(d)
                rev[d["dst"]].append(d)
        return fwd, rev

    def entities(self) -> list[str]:
        return list(self.services) + list(self.datastores)

    def expertise_at(self, pid: str, entity: str, t: int) -> float:
        base = self.expertise.get(pid, {}).get(entity, 0.0)
        if not self.person_active(pid, t):
            return 0.0
        own = self.owner(entity, t)
        if own is not None and self.team_of(pid, t) == own:
            return base
        return base * 0.6  # side expertise or former team: decays

    def index(self):
        self._mem_by_person, self._mem_by_team = defaultdict(list), defaultdict(list)
        for m in self.memberships:
            self._mem_by_person[m["person_id"]].append(m)
            self._mem_by_team[m["team_id"]].append(m)


def _bt(valid_from: int, valid_to: int | None = None, source: str = "catalog") -> dict:
    return {"valid_from": valid_from, "valid_to": valid_to, "recorded_at": valid_from,
            "expired_at": None, "source_id": source}


def build_world(cfg: Config) -> World:
    rng = random.Random(cfg.seed)
    fake = Faker(["en_US", "en_GB", "en_IN", "de_DE", "sv_SE", "nl_NL", "es_ES", "fr_FR"])
    Faker.seed(cfg.seed)
    w = World(cfg=cfg, rng=rng)
    t0 = cfg.history_start
    svc_names = cfg.services or list(C.SERVICES)

    # ---------- regions / services / datastores ----------
    w.regions = [{"id": f"reg_{r}", "name": r} for r in ["eu-west", "us-east", "ap-south"]]
    for name in svc_names:
        dom, tier, lang, entry, cust, aliases = C.SERVICES[name]
        w.services[f"svc_{name}"] = {
            "id": f"svc_{name}", "name": name, "aliases": aliases, "domain": dom, "tier": tier,
            "language": lang, "runtime": "k8s" if rng.random() < 0.9 else "serverless",
            "repo": f"git.{cfg.email_domain}/{dom}/{name}",
            "slo_availability": {0: 99.95, 1: 99.9, 2: 99.5, 3: 99.0}[tier],
            "slo_latency_p99_ms": rng.choice([150, 200, 250, 300, 400, 500]),
            "entry_point": entry, "customer_facing": cust,
            "regions": ["eu-west", "us-east"] + (["ap-south"] if tier <= 1 else []),
            "created_at": t0 - rng.randint(200, 1500) * DAY_MS,
        }
    used_ds = {d for s in svc_names for (d, _, _) in C.DEPENDENCIES.get(s, []) if d in C.DATASTORES}
    for name in C.DATASTORES:
        if cfg.services and name not in used_ds:
            continue
        eng, tier, _ = C.DATASTORES[name]
        w.datastores[f"ds_{name}"] = {"id": f"ds_{name}", "name": name, "engine": eng, "tier": tier,
                                      "aliases": [name.replace("-", " "), name.split("-", 1)[-1]],
                                      "version": {"postgres": "16.4", "redis": "7.4", "elasticsearch": "8.15",
                                                  "cassandra": "4.1", "clickhouse": "24.8", "object-store": "n/a",
                                                  "external-psp": "v3 API"}[eng],
                                      "replicas": rng.choice([1, 2, 3]) if eng != "external-psp" else 0}

    def eid(name: str) -> str:
        return f"svc_{name}" if f"svc_{name}" in w.services else f"ds_{name}"

    # ---------- topology (bi-temporal) ----------
    dep_seen = set()

    def add_dep(src, dst, kind, critical, vf, vt=None, source="catalog"):
        if eid(src) not in w.services and eid(src) not in w.datastores:
            return
        if eid(dst) not in w.services and eid(dst) not in w.datastores:
            return
        rec = {"src": eid(src), "dst": eid(dst), "kind": kind, "critical": critical,
               "p50_ms": rng.choice([2, 4, 8, 15, 25, 40]), "calls_per_min": rng.choice([50, 200, 800, 3000, 12000]),
               **_bt(vf, vt, source)}
        w.dependencies.append(rec)
        dep_seen.add((src, dst))
        return rec

    for src in svc_names:
        for (dst, kind, crit) in C.DEPENDENCIES.get(src, []):
            add_dep(src, dst, kind, crit, t0 - 400 * DAY_MS)
        if src not in C.PLATFORM:
            add_dep(src, "service-mesh", "sync", True, t0 - 400 * DAY_MS)
            add_dep(src, "config-service", "sync", True, t0 - 400 * DAY_MS)
            if src in C.FLAG_CONSUMERS:
                add_dep(src, "feature-flags", "sync", False, t0 - 400 * DAY_MS)
    w.topology_changes = []
    for (ymd, op, src, dst, kind, crit, title) in C.TOPOLOGY_CHANGES:
        t = ms(*ymd, 10)
        if not (cfg.history_start <= t <= cfg.history_end):
            continue
        if op == "add":
            rec = add_dep(src, dst, kind, crit, t, source="decision")
        else:
            rec = None
            for d in w.dependencies:
                if d["src"] == eid(src) and d["dst"] == eid(dst) and d["valid_to"] is None:
                    d["valid_to"] = t
                    d["source_id"] = "decision"
                    rec = d
        if rec:
            w.topology_changes.append({"t": t, "op": op, "src": eid(src), "dst": eid(dst), "title": title})

    # ---------- endpoints & flags ----------
    for sid, s in w.services.items():
        nouns = C.ENDPOINT_NOUNS.get(s["name"], ["status"])
        n = rng.randint(5, 12)
        made = set()
        while len(made) < n:
            noun = rng.choice(nouns)
            method = rng.choice(["GET", "GET", "POST", "PUT", "DELETE"])
            path = f"/v{rng.choice([1, 1, 2])}/{noun}" + rng.choice(["", "/{id}", "/{id}/status", "/batch", "/search"])
            if (method, path) in made:
                if len(made) >= len(nouns) * 6:
                    break
                continue
            made.add((method, path))
            w.endpoints.append({"id": f"ep_{s['name']}_{len(made):02d}", "service_id": sid, "method": method,
                                "path": path, "public": s["entry_point"] or s["customer_facing"] and rng.random() < 0.4,
                                "p99_ms": rng.choice([40, 80, 120, 200, 350, 600])})
    flag_services = [sid for sid, s in w.services.items() if s["name"] in C.FLAG_CONSUMERS]
    n_flags = int(200 * len(flag_services) / len(C.FLAG_CONSUMERS))
    for i in range(n_flags):
        sid = rng.choice(flag_services)
        feat = slug(rng.choice(C.FEATURES))
        created = rng.randint(t0 - 120 * DAY_MS, cfg.history_end - 5 * DAY_MS)
        retired = created + rng.randint(20, 200) * DAY_MS if rng.random() < 0.45 else None
        w.flags.append({"id": f"ff_{i:04d}", "key": f"{w.services[sid]['name']}.{feat}-{i % 97}",
                        "service_id": sid, "created_at": created,
                        "retired_at": retired if retired and retired < cfg.history_end else None,
                        "default": False, "rollout_pct": rng.choice([0, 5, 10, 25, 50, 100])})

    # ---------- teams ----------
    team_keys = [k for k, (_, svcs, _) in C.TEAMS.items()
                 if k == "orbit" or any(f"svc_{s}" in w.services for s in svcs)]
    if not cfg.full_org_events:
        team_keys = [k for k in team_keys if k not in ("orbit", "money")]
    for k in team_keys:
        name, _, size = C.TEAMS[k]
        w.teams[f"team_{k}"] = {"id": f"team_{k}", "key": k, "name": name,
                                "created_at": cfg.orbit_created_at if k == "orbit" else t0 - 900 * DAY_MS,
                                "dissolved_at": cfg.merge_money_at if (k == "money" and cfg.full_org_events) else None}
        w.review_rigor[f"team_{k}"] = round(rng.uniform(0.3, 0.9), 2)
    if not cfg.full_org_events and "team_money" not in w.teams:
        pass

    # ---------- people ----------
    used_names = set()

    def new_person(team_key, role=None, seniority=None, name=None, joined=None):
        nonlocal used_names
        if name is None:
            while True:
                name = f"{fake.first_name()} {fake.last_name()}"
                if name not in used_names and name.split()[0] != "Sam":
                    break
        used_names.add(name)
        pid = f"per_{len(w.people) + 1:04d}"
        if role is None:
            role = rng.choices(list(C.ROLES_MIX), weights=list(C.ROLES_MIX.values()))[0]
            if team_key == "platform" and rng.random() < 0.5:
                role = "sre"
            if team_key == "data" and rng.random() < 0.5:
                role = "data"
        if seniority is None:
            seniority = rng.choices([s for s, _ in C.SENIORITY], weights=[p for _, p in C.SENIORITY])[0]
        first, last = name.split()[0], name.split()[-1]
        handle = slug(f"{first[0]}{last}")[:14] if rng.random() < 0.5 else slug(first)[:12]
        while any(p["handle"] == handle for p in w.people.values()):
            handle = handle + str(rng.randint(1, 9))
        w.people[pid] = {"id": pid, "name": name, "handle": handle, "former_handles": [],
                         "email": f"{handle}@{cfg.email_domain}", "role": role, "seniority": seniority,
                         "timezone": rng.choice(C.TIMEZONES), "persona": rng.choice(C.PERSONAS),
                         "joined_at": joined if joined is not None else t0 - rng.randint(60, 2500) * DAY_MS,
                         "left_at": None}
        return pid

    def join_team(pid, team_key, vf, vt=None, source="hris"):
        w.memberships.append({"person_id": pid, "team_id": f"team_{team_key}", **_bt(vf, vt, source)})

    for k in team_keys:
        if k == "orbit":
            continue
        _, _, size = C.TEAMS[k]
        n = max(3, round(size * cfg.people_scale))
        em = new_person(k, role="em", seniority="staff")
        join_team(em, k, w.people[em]["joined_at"])
        w.teams[f"team_{k}"]["em_id"] = em
        for _ in range(n - 1):
            pid = new_person(k)
            if w.people[pid]["role"] == "em":
                w.people[pid]["role"] = "backend"
            join_team(pid, k, w.people[pid]["joined_at"])
            w.reporting.append({"person_id": pid, "manager_id": em, **_bt(w.people[pid]["joined_at"], None, "hris")})

    # named people for hero scenarios / traps
    if cfg.full_org_events:
        def force(team_key, name, role, seniority, joined=None):
            pid = new_person(team_key, role=role, seniority=seniority, name=name, joined=joined)
            join_team(pid, team_key, w.people[pid]["joined_at"])
            w.reporting.append({"person_id": pid, "manager_id": w.teams[f"team_{team_key}"]["em_id"],
                                **_bt(w.people[pid]["joined_at"], None, "hris")})
            return pid
        w.special["priya"] = force("platform", "Priya Raman", "sre", "senior")
        w.special["vault_expert"] = force("platform", "Tomas Brandt", "sre", "staff")
        w.special["vault_successor"] = force("platform", "Ana Ferreira", "sre", "mid")
        w.special["sam_a"] = force("checkout", "Sam Okafor", "backend", "mid")
        w.special["sam_b"] = force("data", "Sam Lindqvist", "data", "senior")
        w.special["junior_redis"] = force("platform", "Kai Nakamura", "sre", "junior")
        # handle change trap
        hc = rng.choice([p for p in w.people if p not in w.special.values()])
        w.people[hc]["former_handles"] = [{"handle": w.people[hc]["handle"], "until": ms(2026, 3, 1)}]
        w.people[hc]["handle"] = w.people[hc]["handle"] + "-x"
        w.special["handle_changer"] = hc

    # ---------- org history: merge, Orbit, departures, joiners ----------
    w.index()
    if cfg.full_org_events:
        tm = cfg.merge_money_at
        for m in list(w.memberships):
            if m["team_id"] == "team_money" and m["valid_to"] is None:
                m["valid_to"] = tm
                if w.people[m["person_id"]]["role"] == "em":
                    w.people[m["person_id"]]["role"] = "backend"
                    w.people[m["person_id"]]["seniority"] = "staff"
                join_team(m["person_id"], "payments", tm, source="hris")
        w.org_events.append({"id": "org_merge_money", "type": "reorg", "t": tm,
                             "summary": "Money team merges into Payments; ledger, payouts, billing move to Payments"})
        to = cfg.orbit_created_at
        lena = new_person("orbit", role="em", seniority="staff", name="Lena Vogel", joined=to - 30 * DAY_MS)
        w.special["lena"] = lena
        join_team(lena, "orbit", to)
        w.teams["team_orbit"]["em_id"] = lena
        w.index()
        movers = rng.sample([p for p in w.members("team_checkout", to - 1, {"backend"}) if p not in w.special.values()], 2) + \
            rng.sample(w.members("team_discovery", to - 1, {"backend", "frontend"}), 1)
        for pid in movers:
            for m in w.memberships:
                if m["person_id"] == pid and m["valid_to"] is None:
                    m["valid_to"] = to
            join_team(pid, "orbit", to)
        w.special["orbit_movers"] = movers
        w.org_events.append({"id": "org_orbit", "type": "team_created", "t": to,
                             "summary": "New team Orbit (growth) created, owns promotions; Lena Vogel is EM"})
        # joiners (2 of them into Orbit)
        joiner_teams = ["orbit", "orbit"] + rng.choices([k for k in team_keys if k not in ("orbit", "money")], k=cfg.joiners - 2)
        for jt in joiner_teams:
            jt_time = to + rng.randint(1, 80) * DAY_MS if jt == "orbit" else rng.randint(t0 + 10 * DAY_MS, cfg.history_end - 10 * DAY_MS)
            if jt != "orbit" and jt_time >= tm and jt == "money":
                jt = "payments"
            pid = new_person(jt, joined=jt_time, seniority=rng.choice(["junior", "mid", "mid", "senior"]))
            join_team(pid, jt, jt_time)
            w.org_events.append({"id": f"org_join_{pid}", "type": "joiner", "t": jt_time, "person_id": pid,
                                 "team_id": f"team_{jt}", "summary": f"{w.people[pid]['name']} joins {C.TEAMS[jt][0]}"})
        # departures
        leavers = [w.special["vault_expert"]]
        pool = [p for p in w.people if p not in w.special.values() and w.people[p]["role"] not in ("em",)
                and w.people[p]["joined_at"] < t0]
        leavers += rng.sample(pool, cfg.departures - 1)
        for pid in leavers:
            lt = cfg.vault_expert_leaves_at if pid == w.special["vault_expert"] else rng.randint(t0 + 30 * DAY_MS, cfg.history_end - 5 * DAY_MS)
            w.people[pid]["left_at"] = lt
            for m in w.memberships:
                if m["person_id"] == pid and (m["valid_to"] is None or m["valid_to"] > lt) and m["valid_from"] < lt:
                    m["valid_to"] = lt
            w.org_events.append({"id": f"org_leave_{pid}", "type": "departure", "t": lt, "person_id": pid,
                                 "summary": f"{w.people[pid]['name']} leaves the company"})
    else:
        pass

    # ---------- ownership (bi-temporal) ----------
    for k in team_keys:
        for s in C.TEAMS[k][1]:
            if f"svc_{s}" in w.services:
                w.ownership.append({"entity_id": f"svc_{s}", "team_id": f"team_{k}", **_bt(t0 - 900 * DAY_MS)})
    for dsid, d in w.datastores.items():
        tk = C.DATASTORES[d["name"]][2]
        if f"team_{tk}" not in w.teams:
            tk = "platform"
        w.ownership.append({"entity_id": dsid, "team_id": f"team_{tk}", **_bt(t0 - 900 * DAY_MS)})
    if cfg.full_org_events:
        def move(entity, new_team, t, source):
            for o in w.ownership:
                if o["entity_id"] == entity and o["valid_to"] is None:
                    o["valid_to"] = t
            w.ownership.append({"entity_id": entity, "team_id": new_team, **_bt(t, None, source)})
        for e in ["svc_ledger", "svc_payouts", "svc_billing", "ds_pg-ledger", "ds_pg-billing"]:
            move(e, "team_payments", cfg.merge_money_at, "catalog")
        move("svc_promotions", "team_orbit", cfg.orbit_created_at, "catalog")  # chat announces 2 days earlier (trap)
        w.org_events.append({"id": "org_promotions_to_orbit", "type": "ownership_change", "t": cfg.orbit_created_at,
                             "announced_at": cfg.orbit_created_at - 2 * DAY_MS, "entity_id": "svc_promotions",
                             "from_team": "team_checkout", "to_team": "team_orbit",
                             "summary": "promotions ownership moves from Checkout to Orbit"})
    w.index()

    # ---------- hidden expertise / fragility ----------
    for pid, p in w.people.items():
        ex = {}
        base = SENIORITY_BASE[p["seniority"]] * (0.5 if p["role"] in ("em", "pm") else 1.0)
        teams_ever = sorted({m["team_id"] for m in w.memberships if m["person_id"] == pid})
        for tid in teams_ever:
            for o in w.ownership:
                if o["team_id"] == tid:
                    ex[o["entity_id"]] = max(ex.get(o["entity_id"], 0), min(0.98, base * rng.uniform(0.7, 1.15)))
        for e in rng.sample(w.entities(), rng.randint(1, 3)):
            ex[e] = max(ex.get(e, 0), round(rng.uniform(0.2, 0.6), 2))
        if p["role"] == "sre":
            for e in w.entities():
                if e.startswith("ds_") or e.replace("svc_", "") in C.PLATFORM:
                    ex[e] = max(ex.get(e, 0), round(rng.uniform(0.3, 0.7) * (base + 0.3), 2))
        w.expertise[pid] = {k: round(v, 3) for k, v in ex.items()}
    if cfg.full_org_events and "svc_vault" in w.services:
        for pid in w.expertise:
            if "svc_vault" in w.expertise[pid]:
                w.expertise[pid]["svc_vault"] = min(w.expertise[pid]["svc_vault"], 0.3)
        w.expertise[w.special["vault_expert"]]["svc_vault"] = 0.96
        w.expertise[w.special["vault_successor"]]["svc_vault"] = 0.45
        w.expertise[w.special["priya"]].update({"ds_redis-cache": 0.93, "ds_redis-session": 0.8})
        w.expertise[w.special["junior_redis"]].update({"ds_redis-cache": 0.25})
    for e in w.entities():
        rec = w.services.get(e) or w.datastores.get(e)
        f = rng.betavariate(2, 5) + (0.12 if rec["tier"] == 0 else 0) + (0.1 if rec["name"] in ("redis-cache", "config-service", "kafka") else 0)
        w.fragility[e] = round(min(f, 0.95), 3)

    # ---------- on-call ----------
    week = 7 * DAY_MS
    start = cfg.history_start - ((cfg.history_start // DAY_MS + 3) % 7) * DAY_MS + 9 * HOUR_MS  # Monday 09:00
    for tid in w.teams:
        rot_idx = 0
        t = start
        while t <= cfg.test_end:
            elig = [p for p in w.members(tid, t, {"backend", "sre", "data", "frontend", "security"})]
            if len(elig) >= 2:
                elig.sort()
                prim = elig[rot_idx % len(elig)]
                sec = elig[(rot_idx + 1) % len(elig)]
                if rng.random() < 0.05:
                    prim, sec = sec, prim
                for lvl, pid in (("primary", prim), ("secondary", sec)):
                    w.oncall.append({"id": f"oc_{len(w.oncall):05d}", "team_id": tid, "person_id": pid,
                                     "level": lvl, "start": t, "end": t + week})
                rot_idx += 1
            t += week
    return w


def oncall_at(w: World, team_id: str, t: int, level: str = "primary") -> str | None:
    for s in w.oncall:
        if s["team_id"] == team_id and s["level"] == level and s["start"] <= t < s["end"]:
            if w.person_active(s["person_id"], t):
                return s["person_id"]
    if level == "primary":
        return oncall_at(w, team_id, t, "secondary")
    return None

"""Prose rendering from fact cards.

mode="template": deterministic offline renderer (no API key needed). Persona-flavored,
                 with alias (~20%) and typo (~3%) injection so entity resolution is exercised.
mode="gemini":   LLM renderer (google-genai). Same fact cards in, richer text out; cached by
                 sha256(fact card + prompt version) so reruns are free.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from pathlib import Path

from .config import iso
from .world import World

PROMPT_VERSION = "v1"


class Namer:
    def __init__(self, w: World, rng: random.Random):
        self.w, self.rng = w, rng

    def ent(self, eid: str | None) -> str:
        if not eid:
            return "it"
        rec = self.w.services.get(eid) or self.w.datastores.get(eid)
        if rec is None:
            return eid
        r = self.rng.random()
        name = rec["name"]
        if r < 0.20 and rec.get("aliases"):
            name = self.rng.choice(rec["aliases"])
        elif r < 0.23 and len(name) > 5:
            i = self.rng.randrange(1, len(name) - 1)
            name = name[:i] + name[i + 1] + name[i] + name[i + 2:]  # transposition typo
        return name

    def who(self, pid: str | None, t: int | None = None) -> str:
        if not pid or pid not in self.w.people:
            return "someone"
        p = self.w.people[pid]
        h = p["handle"]
        for fh in p.get("former_handles", []):
            if t is not None and t < fh["until"]:
                h = fh["handle"]
        return "@" + h

    def team(self, tid: str | None) -> str:
        return self.w.teams.get(tid or "", {}).get("name", "the team")


def _persona(text: str, persona: str, rng: random.Random) -> str:
    if len(text) < 30 or rng.random() > 0.4:
        return text
    if persona == "terse SRE":
        text = text.replace("I think ", "").replace("Looks like ", "").rstrip(".")
        text = text[:1].lower() + text[1:]
    elif persona == "friendly emoji-heavy PM":
        text = text + " " + rng.choice(["🙏", "🚀", "👀", "✅", "🔥"])
    elif persona == "verbose architect":
        text = text + rng.choice(["", " Worth noting the blast radius here.", " Longer term we should revisit the design."])
    elif persona == "dry humor staff engineer":
        text = text + rng.choice(["", " Fun times.", " Classic."])
    elif persona == "formal manager":
        text = "Team, " + (text[0].lower() + text[1:] if text[1:2].islower() else text)
    return text


def render_message(m: dict, w: World, n: Namer, rng: random.Random) -> str:
    f, it = m["facts"], m["intent"]
    E = n.ent
    if it == "alert_ack":
        s = f"Ack, I'm on it. {', '.join(f.get('alerts', [])) or 'Alerts'} firing. Opening {f['incident']}."
    elif it == "observation":
        es = [E(e) for e in f.get("entities", [])]
        s = f"Seeing elevated errors/latency on {', '.join(es[:-1]) + ' and ' + es[-1] if len(es) > 1 else es[0]}."
    elif it == "wrong_guess":
        s = rng.choice([f"Could this be the {E(f['entity'])} deploy from earlier?", f"Is it {E(f['entity'])}? Its dashboard looks off.",
                        f"My bet is {E(f['entity'])}, checking."])
    elif it == "investigating":
        e = E(f.get("entity"))
        s = rng.choice([f"Checking {e} dashboards.", f"{e} p99 is up ~3x.", f"Nothing obvious in {e} logs.",
                        f"{e} error rate climbing.", f"Looking at recent changes touching {e}.", f"{e} pods look healthy, so probably downstream."])
    elif it == "found_cause":
        if f.get("change_id"):
            ref = rng.choices([f"({f['change_id']})", f"(PR #{f['pr']})" if f.get("pr") else f"({f['change_id']})",
                               f"from {iso(f['change_ts'])[11:16]} UTC" if f.get("change_ts") else f"({f['change_id']})"], [0.6, 0.2, 0.2])[0]
            s = (f"Found it: {f['change_summary']} {ref} by {n.who(f['change_author'], m['ts'])}. "
                 f"{f['mechanism'].capitalize()} on {E(f['root'])}, which explains everything upstream.")
        else:
            s = f"Root cause looks like {f['mechanism']} on {E(f['root'])}. No related deploys in the window."
    elif it == "action":
        rb = f" following {f['runbook']}" if f.get("runbook") else ""
        s = {"rollback": f"Rolling back the change now{rb}.", "flag_off": f"Turning the flag off{rb}.",
             "scale_out": f"Scaling out{rb}.", "renew_cert": f"Renewing the certificate{rb}.",
             "failover_vendor": f"Failing over card traffic to the secondary route{rb}.",
             "failover_zone": f"Shifting traffic away from the bad zone{rb}.", "data_fix": f"Stopping the backfill and repairing rows{rb}.",
             "kill_migration": f"Killing the migration session{rb}.", "restore_index": f"Restoring the dropped index{rb}.",
             "reissue_cert": f"Re-issuing the cert with the full chain{rb}."}.get(f["mitigation"], f"Mitigating{rb}.")
    elif it == "mitigated":
        s = "Metrics recovering, mitigated." if f.get("verified", True) else "Partially recovered, still watching."
    elif it == "resolved":
        s = "Resolving. " + (f"Follow-ups: {', '.join(f['followups'])}." if f.get("followups") else "No follow-ups.")
    elif it == "chat":
        s = rng.choice(["joining", "+1", "customer support is asking for an ETA", "status page updated", "thanks all",
                        "I can take comms", "paging in more help", "brb grabbing coffee", "nice catch", "will write the postmortem"])
    elif it == "propose":
        s = f"Proposal: {f['title']}. Thoughts? It touches {E(f.get('entity'))}."
    elif it == "concern":
        s = rng.choice([f"Concern: migration risk for {E(f['entity'])}.", "Who owns the rollout plan?",
                        "This doubles our on-call surface for a quarter.", f"What happens to {E(f['entity'])} during the cutover?",
                        "Do we have capacity for this next sprint?", "I'd want a load test before we commit."])
    elif it == "option":
        s = rng.choice([f"Alternative: {f.get('option')}. Probably worse though.", f"Did we consider: {f.get('option')}?",
                        f"We tried '{f.get('option')}' last year, it didn't stick.", f"Option B would be to {f.get('option')}."])
    elif it == "agree":
        s = rng.choice(["+1 from me", "Agree, ship it.", "LGTM if we add a rollback plan.", "Works for me.",
                        "Fine by me, as long as we document it.", "Agreed, let's write the ADR."])
    elif it == "question":
        s = rng.choice([f"How does this affect {E(f.get('entity'))} latency?", "What's the cost impact?", "Do we need a migration window?"])
    elif it == "decide":
        s = f"Decision: {f['title']} ({f['key']}). " + (f"This supersedes {f['supersedes'].replace('dec_', 'ADR-')}." if f.get("supersedes") else "Writing it up.")
    elif it == "ask":
        e = E(f["entity"])
        s = {"who_knows": f"Who knows {e} well? Need help with a weird behaviour.",
             "how_to": f"How do I safely restart {e}?", "why": f"Why does {e} depend on that setting?"}[f["qtype"]]
    elif it == "followup_q":
        s = rng.choice(["Follow-up: does that also apply in ap-south?", "And if that doesn't work?", "Is there a dashboard for that?"])
    elif it == "answer":
        e = E(f["entity"])
        base = rng.choice([f"I've worked on {e} a lot, happy to pair.", f"For {e}, check the config first, it's usually that.",
                           f"Yeah {e} is tricky, here's what I do:"])
        if f.get("runbook"):
            base += f" See {f['runbook']}."
        if f.get("refer_to"):
            base += f" Also ask {n.who(f['refer_to'], m['ts'])}."
        s = base
    elif it == "thanks":
        s = rng.choice(["thanks!", "that worked, ty", "perfect, thank you"])
    elif it == "announce":
        s = _announce(f, w, n)
    elif it == "react":
        s = rng.choice(["congrats!", "welcome!", "thanks for the heads up", "noted", "👋", "good luck!"])
    elif it == "standup":
        e = E(f.get("entity")) if f.get("entity") else "tickets"
        s = f"Yesterday: worked on {e}. Today: " + rng.choice(["reviews", "on-call handover", f"more {e}", "load testing"]) + ". No blockers."
    else:
        s = "ok"
    persona = w.people.get(m["author_id"], {}).get("persona", "")
    s = _persona(s, persona, rng)
    if f.get("injection"):
        s = s + " " + f["injection"]
    return s


def _announce(f: dict, w: World, n: Namer) -> str:
    t = f.get("type")
    if t == "ownership_change":
        lena = w.special.get("lena")
        return (f"FYI from {iso(f['t'])[:10]} {n.ent(f['entity_id'])} is owned by {n.team(f['to_team'])} "
                f"(moving from {n.team(f['from_team'])}). Ping {n.who(lena)} for anything urgent.")
    if t == "reorg":
        return "Org update: the Money team merges into Payments. Ledger, payouts and billing are now owned by Payments."
    if t == "team_created":
        return f"Welcome team Orbit! They'll focus on growth, starting with promotions. EM is {n.who(w.special.get('lena'))}."
    if t == "joiner":
        return f"Please welcome {n.who(f['person_id'])} to {n.team(f.get('team_id'))}!"
    if t == "departure":
        return f"Today is {n.who(f['person_id'])}'s last day. Thank you for everything!"
    return f.get("summary", "")


def render_doc(d: dict, w: World, n: Namer, rng: random.Random, tl) -> str:
    f = d["facts"]
    if d["kind"] == "postmortem":
        tlm = f["timeline"]
        cause = (f"Change {f['change_id']} ({f['change_summary']}) authored by {n.who(f['change_author'])} introduced "
                 f"{f['mechanism']} on {n.ent(f['root'])}." if f.get("change_id") else
                 f"{f['mechanism'].capitalize()} on {n.ent(f['root'])}. Not caused by a change.")
        return "\n".join([
            f"# {d['title']}", f"Severity: SEV{f['sev']}", "## Summary",
            f"{n.ent(f['root'])} {'had no alert of its own; ' if f.get('silent_root') else ''}symptoms appeared upstream.",
            "## Impact", f["impact"], "## Root cause", cause,
            "## Detection", f"Paged {n.team(f['detected_by'])} via alerts.",
            "## Resolution", f"{n.who(f['resolver'])} mitigated by {f['mitigation'].replace('_', ' ')}" +
            (f" using runbook {f['runbook']}." if f.get("runbook") else "."),
            "## Timeline (UTC)", f"- {iso(tlm['started'])} start", f"- {iso(tlm['detected'])} detected",
            f"- {iso(tlm['mitigated'])} mitigated", f"- {iso(tlm['resolved'])} resolved",
            "## Action items", *([f"- {k}" for k in f.get("action_items", [])] or ["- none"]),
            "## Lessons", f"- {f['lesson']}"])
    if d["kind"] == "design":
        sup = f" Supersedes {f['supersedes'].replace('dec_', 'ADR-')}." if f.get("supersedes") else ""
        return "\n".join([f"# {d['title']}", "Status: accepted", f"Decider: {n.who(f.get('decider'))}", "## Context", f["context"],
                          "## Decision", f["decision"] + "." + sup, "## Options considered",
                          *(f"- {o} (rejected)" for o in f["options_rejected"]), "## Consequences", f["consequences"]])
    if d["title"].endswith("service overview"):
        deps = ", ".join(n.ent(x) for x in f["depends_on"]) or "nothing"
        return (f"# {d['title']}\nOwned by {n.team(f['owner'])}. Tier {f['tier']}, availability SLO {f['slo']}%.\n"
                f"Depends on: {deps}.")
    if d["title"].endswith("onboarding"):
        return f"# {d['title']}\nOwned services: " + ", ".join(n.ent(e) for e in d["entity_ids"]) + ".\nAsk your EM for access."
    cite = ""
    if f.get("cites_decision"):
        cite = f"\nBackground: see {f['cites_decision'].replace('dec_', 'ADR-')}."
    return f"# {d['title']}\n1. Check the dashboards for {n.ent(f.get('entity'))}.\n2. Follow the standard procedure.{cite}"


def render_ticket(t: dict, w: World, n: Namer, rng: random.Random) -> str:
    f = t["facts"]
    body = f"{t['title']}\n\n" + rng.choice(["Repro attached.", "Seen in prod logs.", "Customer-reported.", "Found during load test.",
                                               "Part of quarterly hygiene.", "Requested by the team."])
    if f.get("incident"):
        body += f" Follow-up from {f['incident'].replace('inc_', 'INC-')}."
    if f.get("injection"):
        body += "\n\n" + f["injection"]
    return body


def render_runbook(rb: dict, n: Namer) -> str:
    return f"# {rb['key']}: {rb['title']}\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(rb["steps"], 1))


def render_all(w: World, tl, mode: str = "template", cache_dir: Path | None = None, concurrency: int = 8):
    rng = random.Random(w.cfg.seed + 2)
    n = Namer(w, rng)
    if mode == "gemini":
        return render_gemini(w, tl, cache_dir or Path(".cache"), concurrency)
    for m in tl.messages:
        m["text"] = render_message(m, w, n, rng)
    for d in tl.docs:
        d["text"] = render_doc(d, w, n, rng, tl)
    for t in tl.tickets:
        t["text"] = render_ticket(t, w, n, rng)
    for rb in tl.runbooks:
        rb["text"] = render_runbook(rb, n)


# ---------------- Gemini renderer ----------------
SYSTEM = ("You write realistic internal engineering artifacts for a FICTIONAL company. Use only the facts given. "
          "Do not invent people, services, ids or numbers that are not in the facts. Keep each person's persona style. "
          "Refer to services sometimes by the provided aliases. Output JSON only.")


def render_gemini(w: World, tl, cache_dir: Path, concurrency: int = 24):  # pragma: no cover - needs API key
    """Thread-level and doc-level LLM rendering. Falls back to templates on any failure."""
    from concurrent.futures import ThreadPoolExecutor

    from google import genai

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY to use --render gemini")
    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL_RENDER", "gemini-3.5-flash-lite")  # fast + good enough for prose rewriting
    cache_dir.mkdir(parents=True, exist_ok=True)
    render_all(w, tl, "template")  # baseline text; LLM output overwrites where it succeeds
    msgs_by_thread = {}
    for m in tl.messages:
        msgs_by_thread.setdefault(m["thread_id"], []).append(m)

    def call(card: dict) -> dict | None:
        h = hashlib.sha256((json.dumps(card, sort_keys=True) + PROMPT_VERSION).encode()).hexdigest()
        p = cache_dir / f"{h}.json"
        if p.exists():
            return json.loads(p.read_text())
        for attempt in range(4):
            try:
                r = client.models.generate_content(model=model, contents=json.dumps(card),
                                                   config={"system_instruction": SYSTEM, "response_mime_type": "application/json",
                                                           "temperature": 0.7})
                out = json.loads(r.text)
                p.write_text(json.dumps(out))
                return out
            except Exception as e:
                stats["errors"] += 1
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    time.sleep(2 ** attempt)
        return None

    stats = {"errors": 0, "done": 0}

    def tick():
        stats["done"] += 1
        if stats["done"] % 250 == 0:
            print(f"  rendered {stats['done']} artifacts ({stats['errors']} retried errors)", flush=True)

    def people_card(pids):
        return {w.people[p]["handle"]: {"name": w.people[p]["name"], "persona": w.people[p]["persona"]} for p in pids if p in w.people}

    def thread_job(th):
        ms = sorted(msgs_by_thread.get(th["id"], []), key=lambda m: m["ts"])
        card = {"task": "Rewrite each message naturally. Return {\"messages\": [{\"id\":..., \"text\":...}]} with the same ids.",
                "channel": th["channel"], "title": th["title"], "people": people_card({m["author_id"] for m in ms}),
                "messages": [{"id": m["id"], "author": w.people.get(m["author_id"], {}).get("handle"), "intent": m["intent"],
                              "draft": m["text"], "must_keep": [x for x in m["text"].split() if any(c.isdigit() for c in x) or x.startswith("@")]}
                             for m in ms]}
        out = call(card)
        if out and isinstance(out.get("messages"), list):
            by = {x.get("id"): x.get("text") for x in out["messages"] if isinstance(x, dict)}
            keep = {c["id"]: c["must_keep"] for c in card["messages"]}
            for m in ms:
                new = by.get(m["id"])
                # accept LLM text only if every id/number/@handle from the draft survived (fact coverage gate)
                if isinstance(new, str) and all(tok.strip(".,()") in new for tok in keep[m["id"]]):
                    inj = m["facts"].get("injection")
                    m["text"] = new if not inj or inj in new else f"{new} {inj}"  # traps must survive the rewrite
        tick()

    def doc_job(d):
        out = call({"task": "Rewrite this document in full natural prose with the same sections. Return {\"text\": ...}.",
                    "kind": d["kind"], "draft": d["text"]})
        if out and isinstance(out.get("text"), str) and len(out["text"]) > 50:
            d["text"] = out["text"]
        tick()

    with ThreadPoolExecutor(concurrency) as ex:
        list(ex.map(thread_job, tl.threads))
        list(ex.map(doc_job, [d for d in tl.docs if d["kind"] in ("postmortem", "design")]))

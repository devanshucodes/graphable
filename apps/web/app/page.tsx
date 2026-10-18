"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ALERT_COLOR, API, ROOT_COLOR, TYPE_COLOR, ago, arrow, clean, fault, fmtDate, fmtTime, get, incKey, nameOf, post, pretty, sentence, short } from "@/lib/api";
import { AGENT, Button, Menu, Meter, Note, PageBar, Person, Prop, Props, Section, Sev, Status, TOOL_LABEL, Tabs, Tag } from "@/components/ui";
import CausalChain, { type CEdge, type CNode } from "@/components/CausalChain";
import NetGraph, { type NEdge, type NNode } from "@/components/NetGraph";
import { AlertTriangle, BellRing, Check, ChevronDown, Clock, Copy, Loader2, Play, RotateCcw, ShieldCheck, Sparkles, Ticket, ThumbsDown, ThumbsUp, Undo2, Users, Wrench, X } from "lucide-react";

type Ev = { seq: number; t: number; type: string; [k: string]: any };
const HERO: Record<string, { title: string; tests: string; tracks: string[] }> = {
  H1: { title: "Repeat offender", tests: "Silent root cause. Memory recalls the past fix and who did it.", tracks: ["Connected data", "Memory"] },
  H2: { title: "Decoy deploy", tests: "Root is a config change on a hub; the newest deploy is innocent.", tracks: ["Connected data"] },
  H3: { title: "The org moved", tests: "A chat message moves ownership. Paging follows; history stays correct.", tracks: ["Company brain", "Memory"] },
  H4: { title: "Not a change", tests: "Certificate expiry. No deploy to blame, and the expert has left.", tracks: ["Connected data", "Company brain"] },
};
const TERMS: Record<string, [string, string]> = {
  Cov: ["Explains the alerts", "Share of alerting services that depend on it, discounted for hubs"],
  F: ["Minimal explanation", "No deeper component explains as much"],
  K: ["Change-type risk (learned)", "How often this kind of change was rolled back or confirmed as a cause"],
  R: ["Recency", "How shortly before detection it landed"],
  H: ["History", "Same kind of change caused incidents here before"],
  P: ["Proximity", "Dependency hops from the symptoms"],
  S: ["Alerting itself", "The component raised its own alert"],
};
const STEPS: [string, string?][] = [["Triage"], ["Investigate"], ["Review", "Gemini + verifier"], ["Respond"], ["Approve"], ["Learn"]];
const EVENTS = ["start", "phase", "tool_call", "handoff", "provisional", "verified", "action", "awaiting_approval", "closed", "result", "error"];
/** The query shape behind each core MCP tool, shown in the Timeline. The tools fill in parameters and post-process in code. */
const CYPHER: Record<string, string> = {
  incident_rank_root_causes: "MATCH p=(a:Component)-[:DEPENDS_ON*0..5]->(c:Component) WHERE a.id IN $alerting AND all(r IN relationships(p) WHERE r.valid_from <= $t0 AND (r.valid_to IS NULL OR r.valid_to > $t0)) WITH c, collect(DISTINCT a.id) AS explained MATCH (ch:Change)-[:CHANGED]->(c) WHERE ch.ts >= $t0 - $window AND ch.ts <= $t0 RETURN c, ch, explained",
  incident_blast_radius: "MATCH p=(up:Component)-[:DEPENDS_ON*1..3]->(s:Component {id:$id}) WHERE all(r IN relationships(p) WHERE r.valid_to IS NULL OR r.valid_to > $at) RETURN DISTINCT up, length(p) AS hops",
  graph_hubs: "CALL algo.pageRank('Component', 'DEPENDS_ON') YIELD node, score RETURN node.id, score ORDER BY score DESC",
  org_ownership: "MATCH (t:Team)-[o:OWNS]->(:Component {id:$id}) WHERE o.valid_from <= $at AND (o.valid_to IS NULL OR o.valid_to > $at) RETURN t, o.source_id",
  incident_similar: "CALL db.idx.vector.queryNodes('Incident', 'embedding', 40, q.embedding) YIELD node, score  // + structural overlap on AFFECTED / FIRED_ON",
  memory_inbox: "MATCH (:Incident {id:$inc})<-[:FOR]-(h:Handoff) WHERE h.to_agent = $role OPTIONAL MATCH (h)-[:CARRIES]->(e:Evidence) RETURN h, collect(e.id)",
  org_experts: "MATCH (p:Person)-[r:RESOLVED_BY|WROTE|AUTHORED|RESPONDED]-(x)-[:AFFECTED|ABOUT|CHANGED]->(:Component {id:$id}) RETURN p, count(x) // weighted with time decay",
};
const cfg = (s?: string) => (s?.includes(": ") ? s.split(": ").slice(1).join(": ") : s || "");
const mins = (m: number) => `${Math.round(m)} min`;
/** A coarse gap in time: 40 min, 5 h, 3 d. */
const dur = (ms: number) => { const m = Math.max(0, ms) / 60000; return m < 90 ? `${Math.round(m)} min` : m < 2880 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`; };
/** Scenario runs have a letter after inc_ (inc_h2_99fb); the seeded history is inc_0397. */
const simulated = (id: string) => /^inc_[a-z]/.test(id);
const scenarioOf = (id: string) => /^inc_(h\d+)_/.exec(id)?.[1].toUpperCase() ?? null;
/** What to tell the user when a request fails: the server's own words, plus what to do about it. */
const errText = (e: any) => {
  const m = sentence(String(e?.message ?? e).replace(/\.$/, ""));
  if (e?.status === 409) return `${m}. Press Reset demo to replay the scenarios on a fresh graph.`;
  return e?.status ? `${m}.` : `${m}. Is the API running on ${API}?`;
};
const spin = (size = 13) => <Loader2 size={size} className="animate-spin" />;

export default function Incidents() {
  const [heroes, setHeroes] = useState<any[]>([]);
  const [ov, setOv] = useState<any>(null);
  const [names, setNames] = useState<Set<string>>(new Set()); // component names: titles that start with one keep its lowercase
  const [sel, setSel] = useState<string | null>(null);
  const [inc, setInc] = useState<any>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [detail, setDetail] = useState<any>(null);
  const [graph, setGraph] = useState<any>(null);
  const [prov, setProv] = useState<any>(null); // the graph's ranking, streamed before Gemini's review finishes
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null); // scenario whose /sim/play request is in flight
  const [loadingId, setLoadingId] = useState<string | null>(null); // incident being reopened from the list
  const [resetting, setResetting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "graph" | "timeline">("timeline");
  const [now, setNow] = useState(Date.now());
  const [took, setTook] = useState<number | null>(null); // server-measured run time; the resume after approval reports none, so the last one stays
  const [fb, setFb] = useState<{ correct: boolean; text: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null); // the approve / reject / feedback request in flight
  const es = useRef<EventSource | null>(null);
  const cur = useRef<string | null>(null); // incident on screen; late responses for any other one are dropped
  const first = useRef(true); // only the first result (or provisional result) of a run moves the view from Timeline to Overview
  const opening = useRef<string | null>(null);

  const loadOv = useCallback(() => get("/overview").then(setOv).catch(() => {}), []);
  useEffect(() => {
    get("/scenarios").then((s) => setHeroes(s.hero)).catch((e) => setErr(errText(e)));
    get("/entities").then((xs: any[]) => setNames(new Set(xs.map((x) => x.name)))).catch(() => {});
    loadOv();
  }, [loadOv]);
  useEffect(() => { if (!busy) return; const i = setInterval(() => setNow(Date.now()), 200); return () => clearInterval(i); }, [busy]);
  useEffect(() => () => es.current?.close(), []);
  const title = (s?: string | null) => (s && names.has(s.split(" ")[0]) ? s : sentence(s));

  const stop = () => { es.current?.close(); es.current = null; setBusy(false); };

  const refresh = useCallback(async (id: string) => {
    const [d, g] = await Promise.all([get(`/incidents/${id}`), get(`/graph/incident/${id}`)]);
    if (cur.current !== id) return null;
    setDetail(d); setGraph(g);
    const r = d.run?.result;
    if (r?.elapsed_s != null) setTook(r.elapsed_s);
    if (r && first.current) { first.current = false; setTab((t) => (t === "timeline" ? "overview" : t)); }
    return d;
  }, []);
  // the stream can end on `closed` a moment before the server stores the run's result, so poll briefly for it
  const settle = useCallback(async (id: string) => {
    for (let i = 0; i < 6; i++) {
      const d = await refresh(id);
      if (!d?.run || d.run.result || d.run.status === "error") return;
      await new Promise((r) => setTimeout(r, 500));
    }
  }, [refresh]);

  /** Follow a run's event stream. The server replays every past event to a new listener, so this also rebuilds a reopened run. */
  const listen = (id: string, replay = false) => {
    const src = new EventSource(`${API}/incidents/${id}/stream`);
    es.current = src;
    let ended = false;
    const finish = () => {
      if (ended) return;
      ended = true; src.close();
      if (es.current !== src) return; // Back, Reset or a newer run already took over
      es.current = null; setBusy(false);
      settle(id).catch((e) => { if (cur.current === id) setErr(errText(e)); });
    };
    const on = (m: MessageEvent) => {
      if (!m.data || es.current !== src) return;
      const ev: Ev = JSON.parse(m.data);
      setEvents((xs) => (xs.some((x) => x.seq === ev.seq) ? xs : [...xs, ev]));
      if (replay && ev.seq === 1) setInc((i: any) => (i?.incident_id === id ? { ...i, startedAt: ev.t * 1000 } : i));
      if (ev.type === "provisional") {
        setProv(ev.investigation);
        if (first.current) { first.current = false; setTab((t) => (t === "timeline" ? "overview" : t)); }
        get(`/graph/incident/${id}`).then((g) => { if (cur.current === id) setGraph((x: any) => x ?? g); }).catch(() => {});
      }
      if (ev.type === "result" && ev.result?.elapsed_s != null) setTook(ev.result.elapsed_s);
      if (ev.type === "error") setErr(`The crew stopped with an error: ${ev.error}`);
      if (ev.type === "result" && ev.status === "awaiting_approval") {
        // keep the stream open: after approval the crew resumes and streams the rest of the run
        setBusy(false); settle(id).catch((e) => { if (cur.current === id) setErr(errText(e)); });
      } else if (ev.type === "result" || ev.type === "error" || ev.type === "closed") finish();
    };
    EVENTS.forEach((t) => src.addEventListener(t, on));
    src.onerror = finish; // also a 404: no live run, so settle() just shows what the graph stored
  };

  const run = async (sid: string) => {
    setErr(null); setBusy(true); setPlaying(sid);
    let r: any;
    try { r = await post("/sim/play", { kind: "hero", sid }); }
    catch (e) { setErr(errText(e)); setBusy(false); return; } // nothing was cleared yet, so the view and the scenario picker stay as they were
    finally { setPlaying(null); }
    stop(); setBusy(true); opening.current = null; setLoadingId(null); // a reopen still loading must not replace the new run
    const id: string = r.incident_id;
    cur.current = id; first.current = true;
    setEvents([]); setDetail(null); setGraph(null); setProv(null); setSel(sid); setFb(null); setPending(null); setTab("timeline"); setTook(null); setNow(Date.now());
    setInc({ ...r, startedAt: Date.now() });
    window.history.pushState(null, "", `?incident=${id}`);
    document.getElementById("panel")?.scrollTo({ top: 0 });
    listen(id);
    // what the graph already knows before the crew reports (e.g. ownership moved by a chat message)
    get(`/incidents/${id}`).then((d) => { if (cur.current === id) setDetail((x: any) => x ?? d); }).catch(() => {});
  };
  /** Reopen a past run: its stored detail, plus the replayed stream when the API still holds the run. */
  const open = async (id: string, push = true) => {
    if (opening.current === id) return;
    opening.current = id; setLoadingId(id); setErr(null);
    try {
      const d = await get(`/incidents/${id}`);
      if (opening.current !== id) return; // another incident was opened meanwhile
      stop(); cur.current = id; first.current = !d.run?.result;
      if (push) window.history.pushState(null, "", `?incident=${id}`);
      const alerts: any[] = d.alerts ?? [];
      setEvents([]); setProv(null); setGraph(null); setDetail(d); setPending(null); setSel(scenarioOf(id)); setNow(Date.now());
      setFb(typeof d.inc?.feedback === "boolean" ? { correct: d.inc.feedback, text: "Answered earlier; the answer is stored on the incident in the graph." } : null);
      setTook(d.run?.result?.elapsed_s ?? null);
      setTab(d.run?.result ? "overview" : "timeline");
      setInc({
        incident_id: id, key: d.inc?.key ?? incKey(id), startedAt: Date.now(),
        intake: { title: d.inc?.title, detected_at: d.inc?.detected_at ?? d.inc?.started_at, n_alerts: alerts.length, alerting: [...new Set(alerts.map((a) => a.component_id))], noise_dropped: null },
      });
      document.getElementById("panel")?.scrollTo({ top: 0 });
      get(`/graph/incident/${id}`).then((g) => { if (cur.current === id) setGraph(g); }).catch(() => {});
      if (d.run) { setBusy(d.run.status === "running" || d.run.status === "resuming"); listen(id, true); }
    } catch (e: any) {
      if (opening.current !== id) return;
      setErr(e?.status === 404 ? `${incKey(id)} is not in the graph. Reset demo clears simulated incidents.` : errText(e));
      if (!push && !cur.current) window.history.replaceState(null, "", window.location.pathname);
    } finally {
      if (opening.current === id) { opening.current = null; setLoadingId(null); }
    }
  };
  const back = (nav = true) => {
    stop(); cur.current = null; opening.current = null;
    setInc(null); setEvents([]); setDetail(null); setGraph(null); setProv(null); setSel(null); setErr(null); setPending(null); setFb(null); setLoadingId(null); loadOv();
    if (nav && window.location.search) window.history.pushState(null, "", window.location.pathname);
  };
  const reset = async () => {
    stop(); setBusy(true); setResetting(true); setErr(null);
    try { await post("/admin/reset"); back(); } catch (e) { setErr(errText(e)); } finally { setBusy(false); setResetting(false); }
  };
  // ?incident=<id> keeps the incident across reloads; Back / Forward move between the list and incidents
  useEffect(() => {
    const fromUrl = () => new URLSearchParams(window.location.search).get("incident");
    const id = fromUrl();
    if (id) open(id, false);
    const pop = () => { const x = fromUrl(); if (x) { if (x !== cur.current) open(x, false); } else if (cur.current) back(false); };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const decide = async (aid: string, d: "approve" | "reject") => {
    if (pending || !inc) return;
    const id = inc.incident_id;
    setPending(`${aid}:${d}`); setErr(null);
    try { await post(`/actions/${aid}/${d}`); await refresh(id); }
    catch (e) { if (cur.current === id) setErr(errText(e)); }
    finally { setPending(null); }
  };
  const feedback = async (correct: boolean) => {
    if (pending || fb || !inc) return;
    const id = inc.incident_id;
    setPending(correct ? "fb:yes" : "fb:no"); setErr(null);
    try {
      await post(`/incidents/${id}/feedback`, { correct });
      if (cur.current !== id) return;
      setFb({ correct, text: correct ? "Recorded. The suspected cause is now a confirmed cause and the lesson was upvoted." : "Recorded. The lesson was downvoted and the hypothesis marked refuted." });
      await refresh(id);
    } catch (e) { if (cur.current === id) setErr(errText(e)); }
    finally { setPending(null); }
  };

  const result = detail?.run?.result;
  const final = result?.investigation;
  const isProv = !final && !!prov; // showing the graph's ranking while Gemini reviews it
  const tri = result?.triage ?? events.find((e: any) => e.type === "handoff" && e.triage)?.triage; // triage is streamed before the final result
  const inv = final ?? prov, ver = result?.verification, resp = result?.response;
  const root = inv?.root;
  const noRun = !!detail && !detail.run; // the API no longer holds this run (it restarted): show what the graph stored
  const rs: string | undefined = detail?.run?.status;
  const status: string = rs && rs !== "running" && rs !== "resuming" ? rs
    : rs || !noRun ? (inc ? "investigating" : "idle")
    : detail.inc?.status === "mitigating" ? "mitigated" : detail.inc?.status || "open";
  const pageRejected = detail?.actions?.some((a: any) => a.kind === "page" && a.status === "rejected");
  const shown = status === "closed" && pageRejected ? "closed_rejected" : status;
  const calls = events.filter((e) => e.type === "tool_call");
  const has = (t: string, f?: (e: Ev) => boolean) => events.some((e) => e.type === t && (!f || f(e)));
  const failed = has("error") || status === "error";
  // the investigator hands off to the responder before Gemini's second-opinion review and the code verifier run
  const investigated = has("verified") || has("tool_call", (e) => e.agent === "investigator" && e.tool === "memory_handoff");
  const closed = has("closed") || status === "closed";
  const step = (i: number): "done" | "active" | "todo" => {
    // a rejected page leaves nothing confirmed to learn from until on-call says whether the diagnosis was right
    const done = [has("handoff", (e) => e.from === "triage"), investigated, has("verified"), has("result") || has("awaiting_approval") || has("closed"), status === "closed", closed && (!pageRejected || !!fb)];
    const started = [has("phase", (e) => e.phase === "triage"), has("phase", (e) => e.phase === "investigate"), investigated, has("phase", (e) => e.phase === "respond"), status === "awaiting_approval" || status === "closed", status === "closed" && !pageRejected];
    return done[i] ? "done" : started[i] ? "active" : "todo";
  };
  const elapsed = took != null ? `${took.toFixed(1)}s` : inc ? `${(Math.max(0, now - inc.startedAt) / 1000).toFixed(1)}s` : "";
  // the most recent change in the window, root or not (the "decoy deploy" story); older APIs only send the candidates
  const nw = useMemo(() => {
    if (!inv) return null;
    if (inv.newest_change !== undefined) return inv.newest_change;
    const c = (inv.candidates ?? []).filter((x: any) => x.change_id && x.minutes_before_detection != null)
      .reduce((a: any, x: any) => (!a || x.minutes_before_detection < a.minutes_before_detection ? x : a), null);
    return c && { change_id: c.change_id, entity_id: c.entity_id, entity_name: c.entity_name, summary: c.change_summary, minutes_before_detection: c.minutes_before_detection, is_root: c.change_id === inv.root?.change_id };
  }, [inv]);
  const decoy = nw && !nw.is_root ? nw : null;

  const chain = useMemo(() => {
    if (!root || !inv || !inc) return null;
    const nodes: CNode[] = [], edges: CEdge[] = [];
    const path: string[] = root.path?.length ? root.path : [root.entity_id];
    const k = path.length;
    nodes.push({ id: "inc", label: inc.key, sub: title(inc.intake?.title), kind: "incident", col: 0, row: 0 });
    path.forEach((p, i) => nodes.push({ id: p, label: nameOf(p), col: i + 1, row: 0, kind: i === k - 1 ? "root" : i === 0 ? "alerting" : "service", sub: i === 0 ? "raised alerts" : "dependency" }));
    edges.push({ from: "inc", to: path[0], label: "alerted on" });
    path.slice(0, -1).forEach((p, i) => edges.push({ from: p, to: path[i + 1], label: "depends on", hot: true }));
    const rc = path[k - 1];
    const rootN = nodes.find((n) => n.id === rc)!;
    rootN.sub = root.type === "change" ? `explains ${Math.round((root.breakdown?.Cov ?? root.breakdown?.E ?? 0) * 100)}% of alerts` : fault(root.fault_type);
    if (root.change_id) {
      const [what, ...rest] = cfg(root.summary).split(" ");
      nodes.push({ id: root.change_id, label: what || "change", sub: arrow(rest.join(" ")) || root.change_id, kind: "change", col: k + 1, row: 0 });
      edges.push({ from: root.change_id, to: rc, label: "changed", hot: true });
      const author = graph?.nodes?.find((n: any) => n.type === "Person" && graph.edges.some((e: any) => e.source === n.id && e.target === root.change_id));
      if (author) { nodes.push({ id: "author", label: author.label, sub: "authored the change", kind: "person", col: k + 2, row: 0 }); edges.push({ from: "author", to: root.change_id, label: "authored" }); }
    }
    if (resp?.owner) {
      nodes.push({ id: "team", label: resp.owner.team, sub: `owns ${nameOf(rc)}`, kind: "team", col: k, row: 1 });
      edges.push({ from: "team", to: rc, label: "owns" });
      if (resp.oncall) { const c = k - 1 >= 1 ? k - 1 : k + 1; nodes.push({ id: "oncall", label: resp.oncall.name, sub: "on call now", kind: "person", col: c, row: 1 }); edges.push({ from: "oncall", to: "team", label: "on call" }); }
    }
    const ex = resp?.experts?.[0];
    if (ex && ex.id !== resp?.oncall?.id) {
      // keep the expert clear of the on-call box and of the on-call -> team arrow
      const pos = !root.change_id ? { col: k + 1, row: 0 } : nodes.some((n) => n.id === "oncall" && n.col === k + 1) ? { col: k + 2, row: 1 } : { col: k + 1, row: 1 };
      nodes.push({ id: "expert", label: ex.name, sub: "top expert (computed)", kind: "person", ...pos }); edges.push({ from: "expert", to: rc, label: "knows", dashed: true });
    }
    const s = inv.similar?.[0];
    if (s) {
      nodes.push({ id: "past", label: s.key, sub: `${Math.round(s.similarity * 100)}% similar`, kind: "past", col: 0, row: 2 });
      edges.push({ from: "inc", to: "past", label: "similar to", dashed: true });
      const f = s.fixed_by?.[0];
      if (f) { nodes.push({ id: "fixer", label: f.name, sub: f.active === false ? "fixed it · has left" : "fixed it last time", kind: "person", col: 1, row: 2 }); edges.push({ from: "past", to: "fixer", label: "resolved by" }); }
      const rb = s.runbooks?.[0];
      if (rb) { nodes.push({ id: "rb", label: rb.key, sub: inv.runbook?.key === rb.key && inv.runbook?.stale ? "stale" : "used to fix it", kind: "runbook", col: 2, row: 2 }); edges.push({ from: f ? "fixer" : "past", to: "rb", label: f ? "used" : "mitigated with" }); }
    }
    return { nodes, edges };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root, inv, inc, graph, resp, names]);

  const ev = useMemo(() => {
    if (!graph || !inc) return null;
    const al = new Set<string>(graph.alerting ?? []), path = new Set<string>(root?.path ?? []);
    const rootIds = new Set([root?.change_id, root?.entity_id].filter(Boolean));
    const decoyId = decoy?.change_id;
    const nodes: NNode[] = graph.nodes.map((n: any) => {
      const isRoot = rootIds.has(n.id), comp = n.type === "Component", ds = n.id.startsWith("ds_");
      return {
        id: n.id, type: comp ? (ds ? "Datastore" : "Service") : n.type,
        // changes drop their "component: " prefix, or five changes on one service all read the same
        label: n.type === "Change" ? arrow(cfg(n.label)) : n.label, detail: arrow(n.label) + (n.id === decoyId ? " · newest, ruled out" : ""),
        layer: n.id === inc.incident_id ? 0 : comp ? (al.has(n.id) && !isRoot ? 1 : 2) : n.type === "Change" ? 3 : n.type === "Incident" ? 4 : 5,
        color: isRoot ? ROOT_COLOR : comp && al.has(n.id) ? ALERT_COLOR : TYPE_COLOR[comp ? (ds ? "Datastore" : "Service") : n.type] ?? "#8b8d98",
        ring: isRoot ? "#f6c4c6" : n.id === inc.incident_id ? "#f6dcc3" : n.id === decoyId ? "#f7dfae" : undefined,
        square: n.type === "Change" || n.type === "Incident", strong: isRoot || n.id === inc.incident_id || path.has(n.id),
      };
    });
    const edges: NEdge[] = graph.edges.map((e: any) => ({
      ...e, faint: e.type === "AFFECTED",
      hot: (e.type === "DEPENDS_ON" && path.has(e.source) && path.has(e.target)) || (e.type === "CHANGED" && e.source === root?.change_id),
    }));
    return { nodes, edges };
  }, [graph, inc, root, decoy]);

  const resetBtn = (
    <Button variant="ghost" icon={resetting ? spin() : <RotateCcw size={13} />} onClick={reset} disabled={busy} aria-label="Reset demo" title="Reset demo">
      <span className="hidden sm:inline">Reset demo</span>
    </Button>
  );

  /* ---------------- incident list ---------------- */
  if (!inc) {
    const c = ov?.counts ?? {};
    const stats: [string, string][] = [
      ["Incidents, 12 months", c.Incident?.toLocaleString() ?? "—"],
      ["Changes", c.Change?.toLocaleString() ?? "—"],
      ["Services and datastores", c.Service ? String(c.Service + c.Datastore) : "—"],
      ["People / teams", c.Person ? `${c.Person} / ${c.Team}` : "—"],
      ["Docs, tickets, chats", c.Message ? (c.Message + c.Ticket + c.Doc).toLocaleString() : "—"],
      ["Graph size", ov ? `${(ov.nodes / 1000).toFixed(1)}k nodes` : "—"],
    ];
    return (
      <>
        <PageBar crumbs={["Incidents"]} actions={<>
          {resetBtn}
          <Menu width={360} trigger={(o, t) => <Button variant="primary" aria-haspopup="menu" aria-expanded={o} onClick={t} disabled={busy || !heroes.length} icon={playing ? spin(12) : <Play size={12} fill="currentColor" />}>Simulate incident<ChevronDown size={13} className="-mr-0.5 opacity-60" /></Button>}>
            {(close) => heroes.map((h) => (
              <button key={h.id} role="menuitem" onClick={() => { close(); run(h.id); }} className="flex w-full gap-3 rounded-md px-2.5 py-2 text-left hover:bg-hover">
                <span className="mono mt-px w-5 text-[11.5px] text-ink-3">{h.id}</span>
                <span className="min-w-0"><span className="block text-[13px] font-medium">{HERO[h.id]?.title}</span><span className="block text-[12px] leading-snug text-ink-3">{HERO[h.id]?.tests}</span></span>
              </button>
            ))}
          </Menu>
        </>} />
        <div className="max-w-[1240px] px-4 py-6 sm:px-6">
          <h1 className="text-[22px] font-semibold tracking-[-0.015em]">Incidents</h1>
          <p className="mt-1 max-w-[720px] text-[13.5px] text-ink-2">An on-call crew (Triage → Investigator → Responder) diagnoses incidents by querying Acme&apos;s FalkorDB graph of services, changes, people and past fixes. Every answer cites the graph path behind it. New here? Press <b className="font-medium text-ink">Run</b> on H1, Repeat offender.</p>
          {err && <div className="mt-5"><Note tone="red">{err}</Note></div>}
          <div className="box mt-6 grid grid-cols-2 overflow-hidden sm:grid-cols-3 xl:grid-cols-6">
            {stats.map(([l, v], i) => (
              <div key={l} className={`px-4 py-3 ${i ? "border-l border-line" : ""}`}>
                <div className="truncate text-[12px] text-ink-3">{l}</div>
                <div className="mt-0.5 text-[17px] font-semibold tracking-[-0.01em] num">{v}</div>
              </div>
            ))}
          </div>

          <Section title="Demo scenarios" count={heroes.length || undefined} className="mt-8" right="Each run replays alerts and changes into the demo graph. Use Reset demo to start fresh.">
            <div className="box divide-y divide-line">
              {heroes.map((h) => (
                <div key={h.id} className="row flex items-center gap-4 px-4 py-3">
                  <span className="mono w-6 text-[12px] text-ink-3">{h.id}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium">{HERO[h.id]?.title}</div>
                    <div className="truncate text-[12.5px] text-ink-2">{HERO[h.id]?.tests}</div>
                  </div>
                  <div className="hidden gap-1 lg:flex">{HERO[h.id]?.tracks.map((t) => <Tag key={t}>{t}</Tag>)}</div>
                  <Button disabled={busy} onClick={() => run(h.id)} icon={playing === h.id ? spin(11) : <Play size={11} fill="currentColor" />}>Run</Button>
                </div>
              ))}
              {!heroes.length && <div className="p-4"><div className="skeleton h-10" /></div>}
            </div>
          </Section>

          <Section title="All incidents" count={c.Incident?.toLocaleString()} className="mt-8" right={`showing latest ${ov?.recent_incidents?.length ?? 0}`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-[13px]">
                <thead>
                  <tr className="border-b border-line text-left text-[12px] text-ink-3">
                    <th className="h-8 w-[120px] pl-2 font-normal">ID</th><th className="font-normal">Title</th>
                    <th className="w-[96px] font-normal">Severity</th><th className="w-[128px] font-normal">Status</th><th className="w-[96px] pr-2 text-right font-normal">Started</th>
                  </tr>
                </thead>
                <tbody>
                  {(ov?.recent_incidents ?? []).map((i: any) => {
                    const sim = simulated(i.id);
                    return (
                      <tr key={i.id} className={`h-10 border-b border-line ${sim ? "row cursor-pointer" : ""}`} title={sim ? `Open ${i.key}` : undefined}
                        onClick={sim ? () => open(i.id) : undefined} tabIndex={sim ? 0 : undefined} onKeyDown={sim ? (e) => { if (e.key === "Enter") open(i.id); } : undefined}>
                        <td className="mono pl-2 text-[12.5px] text-ink-2"><span className="inline-flex items-center gap-1.5">{i.key}{loadingId === i.id && spin(11)}</span></td>
                        <td className="pr-4"><span className="line-clamp-1">{title(i.title)}</span></td>
                        <td><Sev level={i.sev} /></td>
                        <td><Status s={i.status} /></td>
                        <td className="pr-2 text-right text-ink-3 num" title={fmtTime(i.started_at)}>{sim ? "Simulated" : ago(i.started_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Section>
        </div>
      </>
    );
  }

  /* ---------------- live incident ---------------- */
  const departed = resp?.departed_experts?.[0];
  const hub = tri?.hubs?.[0];
  const others: any[] = (inv?.candidates ?? []).filter((c: any) => !root?.change_id || c.change_id !== root.change_id).slice(0, 5);
  const moves: any[] = detail?.ownership_moves ?? [];
  const detected: number | undefined = inc.intake?.detected_at;
  const waiting = status === "awaiting_approval" ? detail?.actions?.find((a: any) => a.status === "awaiting_approval") : null;
  const pend = noRun ? <span className="text-ink-3">—</span> : <Pending />;
  const similar: any[] = inv?.similar ?? []; // the investigator's order, same as the Memory row in How it connects
  return (
    <>
      <PageBar crumbs={[{ label: "Incidents", onClick: () => back() }, inc.key]} actions={<>
        {playing && <Loader2 size={14} className="animate-spin text-ink-3" />}
        <select value={sel ?? ""} disabled={busy} onChange={(e) => run(e.target.value)} aria-label="Scenario"
          className="h-7 max-w-[140px] rounded-md border border-line-2 bg-panel px-2 text-[12.5px] shadow-[0_1px_1px_rgba(0,0,0,0.03)] outline-none focus-visible:ring-2 focus-visible:ring-[#c4bbf0] sm:max-w-none">
          {!sel && <option value="" disabled>Run a scenario…</option>}
          {heroes.map((h) => <option key={h.id} value={h.id}>{h.id} · {HERO[h.id]?.title}</option>)}
        </select>
        {resetBtn}
      </>} />
      <div className={`grid min-h-[calc(100%-48px)] ${tab === "graph" ? "" : "xl:grid-cols-[minmax(0,1fr)_320px]"}`}>
        <div className="min-w-0 px-4 pb-12 pt-6 sm:px-8">
          <div className="text-[12.5px] text-ink-3">
            <div className="flex flex-wrap items-center gap-x-2"><span className="mono">{inc.key}</span><span>·</span><span>Detected {fmtTime(detected)}</span></div>
            <div className="mt-0.5 flex flex-wrap gap-x-4">
              <span>{inc.intake?.n_alerts} alerts across {inc.intake?.alerting?.length} services{inc.intake?.noise_dropped != null ? `, ${inc.intake.noise_dropped} noise dropped` : ""}</span>
              {!noRun && <span className="num"><span className="text-ink-2">{calls.length}</span> MCP tool {calls.length === 1 ? "call" : "calls"} in <span className="text-ink-2">{elapsed}</span></span>}
            </div>
          </div>
          <h1 className="mt-1.5 text-[22px] font-semibold tracking-[-0.015em]">{title(inc.intake?.title)}</h1>
          {err && <div className="mt-4"><Note tone="red">{err}</Note></div>}
          {noRun && <div className="mt-4"><Note tone="gray">The API no longer holds this run (it was restarted), so only what the graph stored is shown.</Note></div>}

          <div className="mt-4 flex items-center rounded-lg border border-line px-3.5 py-2.5">
            <ol className="flex flex-wrap items-center gap-y-1">
              {STEPS.map(([s, sub0], i) => {
                const sub = i === 2 && ov && !ov.llm ? "code verifier" : sub0; // graph-only mode: no Gemini review
                const st = step(i), human = i === 4 && st === "active", rejected = i === 4 && st === "done" && pageRejected, dead = st === "active" && failed;
                return (
                  <li key={s} className="flex items-center" title={sub}>
                    <span className={`flex items-center gap-1.5 text-[12.5px] ${st === "todo" ? "text-ink-3" : st === "active" ? "font-medium text-ink" : "text-ink-2"}`}>
                      {rejected || dead ? <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-red"><X size={9} strokeWidth={3.5} className="text-white" /></span>
                        : st === "done" ? <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-green"><Check size={9} strokeWidth={3.5} className="text-white" /></span>
                        : human ? <i className="pulse h-2 w-2 rounded-full bg-brand text-brand" />
                        : st === "active" ? <Loader2 size={14} className="animate-spin text-brand" />
                        : <span className="h-3.5 w-3.5 rounded-full border border-dashed border-ink-3" />}
                      {rejected ? "Page rejected" : human ? "Waiting for approval" : s}
                      {sub && <span className="hidden text-[11.5px] font-normal text-ink-3 min-[1360px]:inline">{sub}</span>}
                    </span>
                    {i < STEPS.length - 1 && <span className="mx-2 h-px w-3 bg-line-2" />}
                  </li>
                );
              })}
            </ol>
          </div>
          {waiting && <ApprovalBar a={waiting} resp={resp} decide={decide} pending={pending} />}

          {status === "closed" && (
            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-line bg-sunken px-3.5 py-2 text-[13px]">
              <Status s={shown} />
              <span className="text-ink-3">·</span>
              <span className="text-ink-2">Was the diagnosis right?</span>
              <span className="flex gap-2">
                {([[true, "Correct", ThumbsUp, "fb:yes", "border-[#c5ead6]! bg-[#effaf4]! text-[#18794e]!"], [false, "Wrong", ThumbsDown, "fb:no", "border-[#fbd5d6]! bg-[#fff3f3]! text-[#ce2c31]!"]] as const).map(([ok, label, Icon, key, on]) => (
                  <Button key={label} aria-pressed={fb?.correct === ok} disabled={!!pending || !!fb} onClick={() => feedback(ok)}
                    icon={pending === key ? spin(12) : <Icon size={13} />} className={fb?.correct === ok ? on : ""}>{label}</Button>
                ))}
              </span>
              <span className="text-[12px] leading-snug text-ink-3">{fb?.text ?? "Your answer is written to the graph and changes what the crew learns."}</span>
            </div>
          )}

          <div className="mt-6">
            <Tabs value={tab} onChange={setTab} tabs={[
              { key: "overview", label: "Overview", disabled: !result && !prov },
              { key: "graph", label: "Evidence graph", disabled: !ev, count: ev?.nodes.length },
              { key: "timeline", label: "Timeline", count: calls.length },
            ]} />
          </div>

          <div className="pt-6">
            {tab === "overview" && (!root ? <div className="space-y-3"><div className="skeleton h-28" /><div className="skeleton h-64" /></div> : (
              <div className="fade-in space-y-9">
                <RootCause inv={inv} root={root} ver={ver} nw={nw} prov={isProv} live={busy} />
                {chain && (
                  <Section title="How it connects" right={<span className="flex items-center gap-3"><span className="flex items-center gap-1.5"><i className="h-0.5 w-3.5 bg-red" />ranked path</span><span className="flex items-center gap-1.5"><i className="h-0 w-3.5 border-t border-dashed border-[#8f7fe0]" />memory / expertise</span></span>}>
                    <div className="box overflow-x-auto bg-sunken p-4"><CausalChain nodes={chain.nodes} edges={chain.edges} rowLabels={["Cause", "Ownership", "Memory"]} /></div>
                  </Section>
                )}
                <Claims inv={inv} ver={ver} prov={isProv} />
                {resp?.status_update && (
                  <Section title="Status update" right={<Button variant="ghost" icon={<Copy size={12} />} onClick={() => navigator.clipboard?.writeText(clean(resp.status_update))}>Copy</Button>}>
                    <div className="box px-4 py-3 text-[13px] leading-6 text-ink-2">{clean(resp.status_update)}</div>
                  </Section>
                )}
                {others.length > 0 && (
                  <Section title="Other suspects" count={others.length} right="ranked lower by the same scoring">
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[520px] text-[13px]">
                        <thead><tr className="border-b border-line text-left text-[12px] text-ink-3"><th className="h-8 w-[160px] font-normal">Component</th><th className="font-normal">Change</th><th className="w-[100px] font-normal">Landed</th><th className="w-[140px] text-right font-normal">Score</th></tr></thead>
                        <tbody>{others.map((c: any) => (
                          <tr key={c.change_id ?? c.entity_id} className="h-10 border-b border-line">
                            <td className="mono text-[12.5px]">{c.entity_name}</td>
                            <td className="pr-4"><span className="flex min-w-0 items-center gap-2"><span className="truncate text-ink-2">{arrow(cfg(c.change_summary))}</span>{c.change_id && c.change_id === nw?.change_id && <Tag tone="amber">Newest change</Tag>}</span></td>
                            <td className="text-ink-3 num">{c.minutes_before_detection != null ? `${mins(c.minutes_before_detection)} before` : "—"}</td>
                            <td><div className="flex items-center justify-end gap-2.5"><Meter value={c.score} color="#b4b4ae" width={72} /><span className="mono w-8 text-right text-ink-2 num">{c.score.toFixed(2)}</span></div></td>
                          </tr>))}</tbody>
                      </table>
                    </div>
                  </Section>
                )}
              </div>
            ))}
            {tab === "graph" && ev && (
              <div className="fade-in">
                <NetGraph nodes={ev.nodes} edges={ev.edges}
                  height={Math.max(440, Math.min(660, 110 + 26 * Math.max(...[0, 1, 2, 3, 4, 5].map((l) => ev.nodes.filter((n) => n.layer === l).length))))}
                  lanes={{ 0: "Incident", 1: "Alerting", 2: "Dependencies", 3: "Changes", 4: "Past incidents", 5: "People, runbooks" }} />
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-ink-2">
                  {([["Root cause", ROOT_COLOR], ["Alerting", ALERT_COLOR], ["Service", TYPE_COLOR.Service], ["Datastore", TYPE_COLOR.Datastore], ["Change", TYPE_COLOR.Change, 1], ["Incident", TYPE_COLOR.Incident, 1], ["Person", TYPE_COLOR.Person], ["Runbook", TYPE_COLOR.Runbook]] as [string, string, number?][]).map(([l, c, sq]) => (
                    <span key={l} className="flex items-center gap-1.5"><i className={`h-2 w-2 ${sq ? "rounded-[2px]" : "rounded-full"}`} style={{ background: c }} />{l}</span>
                  ))}
                  <span className="ml-auto text-ink-3">{ev.nodes.length} nodes and {ev.edges.length} relationships returned by the agents&apos; MCP tools</span>
                </div>
              </div>
            )}
            {tab === "timeline" && <Timeline events={events} busy={busy} moves={moves} detected={detected} />}
          </div>
        </div>

        <aside className={`min-w-0 space-y-8 border-t border-line bg-sunken px-4 pb-10 pt-6 sm:px-6 xl:border-l xl:border-t-0 ${tab === "graph" ? "hidden" : ""}`}>
          <Section title="Properties">
            <Props>
              <Prop k="Status"><Status s={shown} /></Prop>
              <Prop k="Severity">{tri ? <Sev level={tri.severity} /> : pend}</Prop>
              <Prop k="Root cause">{root ? <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-red" /><span className="mono whitespace-nowrap text-[12.5px]">{root.entity_name}</span></span> : pend}</Prop>
              {root?.change_id && <Prop k="Change"><span className="mono whitespace-nowrap text-[12.5px]">{root.change_id}</span></Prop>}
              {hub && (() => {
                const name = hub.name ?? nameOf(hub.id), of = tri.n_components ? ` of ${tri.n_components}` : "";
                return (
                  <Prop k="Hub rank (PageRank)">
                    <span className="block truncate" title={`${name} ranks #${hub.rank}${of} by FalkorDB PageRank over the dependency graph`}>
                      <span className="mono text-[12.5px]">{name}</span><span className="text-ink-3"> · #{hub.rank}{of}</span>
                    </span>
                  </Prop>
                );
              })()}
              <Prop k="Owner">{resp ? resp.owner?.team ?? "—" : pend}</Prop>
              <Prop k="On call">{resp ? <Person name={resp.oncall?.name} /> : pend}</Prop>
              <Prop k="Top expert">{resp ? <Person name={resp.experts?.[0]?.name} /> : pend}</Prop>
              <Prop k="Runbook">{inv ? (inv.runbook ? <span className="flex items-center gap-2"><span className="mono whitespace-nowrap text-[12.5px]">{inv.runbook.key}</span><Tag tone={inv.runbook.stale ? "amber" : "green"}>{inv.runbook.stale ? "Stale" : "Verified"}</Tag></span> : "—") : pend}</Prop>
              <Prop k="Entry points">{tri ? <span className="mono text-[12.5px]">{tri.entry_points?.join(", ") || "—"}</span> : pend}</Prop>
            </Props>
            {moves.map((m, i) => {
              const via = [m.source_channel, m.source_author].filter(Boolean).join(", ");
              const pages = (m.oncall ?? []).map((o: any) => o.name).filter(Boolean).join(", ");
              return (
                <div key={`${m.entity_id}-${i}`} className="mt-3">
                  <Note tone="violet" icon={<Users size={13} />}>
                    <span className="font-medium">{m.entity_name}</span> moved from {m.from_team} to {m.to_team}{" "}
                    {detected && m.valid_from ? `${dur(detected - m.valid_from)} before this incident` : "before this incident"}{via ? ` (${via})` : ""}.
                    {" "}Its alerts now page {pages || "the new on-call"} ({m.to_team}); history is kept.
                  </Note>
                </div>
              );
            })}
            {!moves.length && resp?.owner?.source && resp.owner.source !== "catalog" && (
              <div className="mt-3"><Note tone="violet" icon={<Users size={13} />}>Ownership of {root?.entity_name ?? "this component"} moved to {resp.owner.team} via chat on {fmtDate(resp.owner.since)}; pages follow the new owner. History is kept.</Note></div>
            )}
            {resp && resp.bus_factor <= 1 && (
              <div className="mt-3"><Note icon={<AlertTriangle size={13} />}>Bus factor {resp.bus_factor}.{departed ? ` ${departed.name}, a top expert on this, has left the company.` : ""}</Note></div>
            )}
          </Section>

          <Section title="Actions" right="gated by policy">
            {!detail?.actions?.length ? (noRun ? <p className="text-[12.5px] text-ink-3">No actions recorded.</p> : <div className="skeleton h-28" />) : (
              <div className="box divide-y divide-line">
                {detail.actions.map((a: any) => <ActionRow key={a.id} a={a} resp={resp} decide={decide} pending={pending} />)}
              </div>
            )}
          </Section>

          {inv && (
            <Section title="Similar incidents" count={similar.length || undefined}>
              {similar.length ? (
                <div className="space-y-1">
                  {similar.map((s: any) => (
                    <div key={s.incident_id} className="min-w-0 py-1.5">
                      <div className="flex items-center justify-between gap-2 text-[12.5px]"><span className="mono font-medium">{s.key}</span><span className="text-ink-3 num">{Math.round(s.similarity * 100)}% match</span></div>
                      <div className="truncate text-[12.5px] text-ink-2">{title(s.title)}</div>
                      <div className="mt-1 flex min-w-0 items-center gap-3 text-[12px] text-ink-3">
                        {s.fixed_by?.[0] && <Person name={s.fixed_by[0].name} size={16} dim={s.fixed_by[0].active === false} sub={s.fixed_by[0].active === false ? "left" : undefined} />}
                        {s.runbooks?.[0] && <span className="mono shrink-0 whitespace-nowrap">{s.runbooks[0].key}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              ) : <p className="text-[12.5px] text-ink-3">No close match in memory.</p>}
            </Section>
          )}
        </aside>
      </div>
    </>
  );
}

const Pending = () => <span className="skeleton inline-block h-3.5 w-24 align-middle" />;

function RootCause({ inv, root, ver, nw, prov, live }: any) {
  const change = root.type === "change";
  const unexplained = !change && (!root.fault_type || root.fault_type === "unknown");
  const c0 = inv.candidates?.find((c: any) => c.change_id && c.change_id === root.change_id);
  const factors = Object.entries(root.breakdown ?? {}).filter(([k]) => k in TERMS) as [string, number][];
  const conf = inv.confidence ?? root.score;
  const decoy = nw && !nw.is_root ? nw : null;
  return (
    <div className="box overflow-hidden">
      <div className="flex flex-col gap-4 p-5 sm:flex-row sm:gap-6">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
            <span className="inline-flex items-center gap-1.5 font-medium text-[#ce2c31]"><i className="h-1.5 w-1.5 rounded-full bg-red" />Root cause</span>
            <span>·</span><span>{change ? "Change-induced" : "Not caused by a change"}</span>
            {prov ? <Tag tone="gray" title="The graph's ranking. Gemini is reviewing it and the code verifier checks every claim next.">{live ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}Provisional · Gemini reviewing</Tag>
              : inv.contested ? <Tag tone="amber"><Sparkles size={11} />Gemini disagrees, needs review</Tag> : inv.llm?.decision && !unexplained ? <Tag tone="violet"><Sparkles size={11} />Gemini review agrees</Tag> : null}
          </div>
          <div className="mt-2 text-[18px] font-semibold leading-snug tracking-[-0.01em]">
            {change ? <>{arrow(cfg(root.summary))} <span className="font-normal text-ink-3">on</span> <span className="whitespace-nowrap">{root.entity_name}</span></>
              : <>{sentence(fault(root.fault_type))} <span className="font-normal text-ink-3">on</span> <span className="whitespace-nowrap">{root.entity_name}</span></>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-ink-2">
            {change && <span className="mono">{root.change_id}</span>}
            {c0?.minutes_before_detection != null && <><span className="text-ink-3">·</span><span>landed {mins(c0.minutes_before_detection)} before detection</span></>}
            {change && nw?.is_root && <Tag tone="amber">Newest change</Tag>}
            {change && decoy && <Tag tone="amber">Not the newest change</Tag>}
            {!change && <span>No change in the window scored above the threshold, so the crew did not blame a deploy.</span>}
            {root.path?.length > 1 && <><span className="text-ink-3">·</span><span>impact path {root.path.map(nameOf).join(" → ")}</span></>}
          </div>
          {decoy && (
            <div className="mt-1.5 text-[12.5px] leading-snug text-ink-2">
              Newest change: <span className="text-ink">{arrow(cfg(decoy.summary))}</span> on <span className="mono">{decoy.entity_name}</span>, {mins(decoy.minutes_before_detection)} before detection — ruled out: it does not explain the alerts.
            </div>
          )}
        </div>
        <div className="shrink-0 sm:text-right">
          <div className="text-[28px] font-semibold leading-none tracking-tight num">{Math.round(conf * 100)}<span className="text-[15px] font-normal text-ink-3">%</span></div>
          <div className="mt-1 text-[12px] text-ink-3">confidence</div>
          {ver && <div className="mt-2.5"><Tag tone="green"><ShieldCheck size={11} />{ver.n_verified}/{ver.n_claims} claims verified</Tag></div>}
        </div>
      </div>
      <div className="grid gap-x-10 gap-y-2.5 border-t border-line bg-sunken px-5 py-4 sm:grid-cols-2">
        {factors.map(([k, v]) => (
          <div key={k} title={TERMS[k][1]} className="grid grid-cols-[minmax(0,1fr)_88px_30px] items-center gap-3 text-[12.5px]">
            <span className="truncate text-ink-2">{TERMS[k][0]}</span><Meter value={Number(v)} color={k === "K" ? "#6e56cf" : "#1c1c1a"} /><span className="mono text-right num">{Number(v).toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Claims({ inv, ver, prov }: any) {
  return (
    <Section title="Claims" count={ver ? `${ver.n_verified} of ${ver.n_claims} verified` : prov ? "pending verification" : undefined}
      right={prov ? "the code verifier checks every edge after Gemini's review" : "each claim cites tool evidence; code re-checks every edge"}>
      <div className="box divide-y divide-line">
        {(inv.claims ?? []).map((c: any, i: number) => (
          <div key={i} className="flex gap-3 px-4 py-3">
            {prov
              ? <span title="Pending verification" className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#f3f3f1]"><Clock size={10} strokeWidth={2.5} className="text-ink-3" /></span>
              : c.verified
              ? <span className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#e3f5eb]"><Check size={10} strokeWidth={3} className="text-[#18794e]" /></span>
              : <span className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#fde8e8]"><X size={10} strokeWidth={3} className="text-[#ce2c31]" /></span>}
            <div className="min-w-0 flex-1 text-[13px] leading-relaxed">
              <div className={prov || c.verified ? "text-ink" : "text-ink-2"}>{clean(c.text)}</div>
              {!prov && !c.verified && c.reasons?.length > 0 && <div className="mt-0.5 text-[12px] text-[#ce2c31]">Rejected by the verifier: {c.reasons.map(clean).join("; ")}</div>}
              <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11.5px] text-ink-3">
                <span className="inline-flex items-center gap-1">{c.role === "llm_rationale" ? <><Sparkles size={10} />Gemini</> : "Graph ranker"}</span><span>·</span>
                <span className="mono truncate">{(c.evidence_ids ?? []).join("  ")}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

const ACTION: Record<string, [string, any]> = { ticket: ["File a ticket", Ticket], page: ["Page on-call", BellRing], "remediation:rollback": ["Roll back the change", Undo2] };
/** An action's label and icon; its target (a graph id) as a person or team name when it is one. */
function describe(a: any, resp: any) {
  const [label, Icon] = ACTION[a.kind] ?? [sentence(a.kind.replace("remediation:", "").replaceAll("_", " ")), Wrench];
  const t = String(a.target ?? "");
  const person = [resp?.oncall, ...(resp?.experts ?? []), ...(resp?.departed_experts ?? [])].find((p: any) => p?.id === t);
  const who: string | null = person?.name ?? (t === resp?.owner?.team_id ? resp.owner.team : t.startsWith("team_") ? sentence(short(t)) : null);
  return { label, Icon, who, target: t };
}

function ActionRow({ a, resp, decide, pending }: { a: any; resp: any; decide: (id: string, d: "approve" | "reject") => void; pending: string | null }) {
  const waiting = a.status === "awaiting_approval";
  const { label, Icon, who, target } = describe(a, resp);
  const tone: Record<string, string> = { executed: "green", approved: "green", awaiting_approval: "violet", rejected: "red", proposed: "gray" };
  // components and changes stay as (mono) ids
  return (
    <div className={`px-3 py-2.5 ${waiting ? "bg-[#faf8ff]" : ""}`}>
      <div className="flex items-center gap-2.5">
        <Icon size={14} className="shrink-0 text-ink-3" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{label}</div>
          <div className="truncate text-[11.5px] text-ink-3">Tier {a.tier?.slice(1)} · {who ?? <span className="mono">{nameOf(target)}</span>}</div>
        </div>
        <Tag tone={tone[a.status] ?? "gray"}>{waiting ? "Needs approval" : sentence(a.status.replaceAll("_", " "))}</Tag>
      </div>
      {waiting && (
        <div className="mt-2.5 flex gap-2 pl-6">
          <Button variant="primary" className="flex-1" disabled={!!pending} icon={pending === `${a.id}:approve` ? spin(12) : undefined} onClick={() => decide(a.id, "approve")}>Approve</Button>
          <Button className="flex-1" disabled={!!pending} icon={pending === `${a.id}:reject` ? spin(12) : undefined} onClick={() => decide(a.id, "reject")}>Reject</Button>
        </div>
      )}
    </div>
  );
}

/** Below xl the Actions panel sits far down the page, so the pending decision sticks under the page bar. */
function ApprovalBar({ a, resp, decide, pending }: { a: any; resp: any; decide: (id: string, d: "approve" | "reject") => void; pending: string | null }) {
  const { label, Icon, who, target } = describe(a, resp);
  return (
    <div className="sticky top-12 z-10 mt-2.5 flex items-center gap-2.5 rounded-lg border border-[#e1dafa] bg-[#faf8ff] px-3 py-2 shadow-[0_2px_8px_rgba(0,0,0,0.06)] xl:hidden">
      <Icon size={14} className="shrink-0 text-[#5746af]" />
      <div className="min-w-0 flex-1 leading-tight">
        <div className="truncate text-[13px] font-medium">{label}</div>
        <div className="truncate text-[11.5px] text-ink-3">Needs approval · {who ?? nameOf(target)}</div>
      </div>
      <Button variant="primary" disabled={!!pending} icon={pending === `${a.id}:approve` ? spin(12) : undefined} onClick={() => decide(a.id, "approve")}>Approve</Button>
      <Button disabled={!!pending} icon={pending === `${a.id}:reject` ? spin(12) : undefined} onClick={() => decide(a.id, "reject")}>Reject</Button>
    </div>
  );
}

const ROWS = ["phase", "tool_call", "handoff", "verified", "awaiting_approval", "closed", "error"];
const OWNS = (team: string, c: string) => <span className="mono text-[12px]">({team})-[:OWNS]-&gt;({c})</span>;

function Timeline({ events, busy, moves, detected }: { events: Ev[]; busy: boolean; moves: any[]; detected?: number }) {
  const t0 = events[0]?.t ?? 0;
  const gate = events.find((e) => e.type === "awaiting_approval")?.seq; // the gate re-emits this when the crew resumes; show it once
  const rows: (Ev & { n: number })[] = [];
  for (const e of events) {
    if (!ROWS.includes(e.type) || (e.type === "awaiting_approval" && e.seq !== gate)) continue;
    const prev = rows[rows.length - 1];
    if (e.type === "tool_call" && prev?.type === "tool_call" && prev.agent === e.agent && prev.tool === e.tool && JSON.stringify(prev.args) === JSON.stringify(e.args)) { prev.n++; continue; }
    rows.push({ ...e, n: 1 });
  }
  // after its handoff, further investigator calls come from the Gemini second-opinion review
  const last = events[events.length - 1];
  const reviewing = busy && (last?.agent === "investigator" || last?.type === "provisional") && events.some((e) => e.type === "tool_call" && e.agent === "investigator" && e.tool === "memory_handoff");
  if (!rows.length && !moves.length) {
    return busy
      ? <div className="flex items-center gap-2 py-8 text-[13px] text-ink-2"><Loader2 size={14} className="animate-spin" /> Correlating alerts and starting triage…</div>
      : <div className="py-8 text-[13px] text-ink-3">No agent activity was recorded for this run.</div>;
  }
  return (
    <ol className="max-w-[900px]">
      {moves.map((m, i) => (
        // ownership changed by the chat ingestor before the incident: the reason paging goes to a new team
        <li key={`mv${i}`} className="fade-in grid grid-cols-[56px_minmax(0,1fr)] gap-x-4">
          <div className="mono pt-[5px] text-right text-[11.5px] text-ink-3 num" title={fmtTime(m.valid_from)}>{detected && m.valid_from ? `−${dur(detected - m.valid_from)}` : "before"}</div>
          <div className="relative border-l border-line pb-3.5 pl-5">
            <span className="absolute -left-[4.5px] top-[9px] h-2 w-2 rounded-full ring-[3px] ring-panel" style={{ background: TYPE_COLOR.Team }} />
            <div className="pt-0.5 text-[13px] leading-relaxed">
              <span className="font-medium">Ingestor</span> <span className="text-ink-2">applied a chat message:</span>{" "}
              {OWNS(m.from_team, m.entity_name)} <span className="text-ink-2">closed ·</span> {OWNS(m.to_team, m.entity_name)} <span className="text-ink-2">opened</span>
            </div>
            <div className="mono text-[11.5px] text-ink-3">{[m.source_channel, m.source_author, m.source_id].filter(Boolean).join(" · ")}</div>
          </div>
        </li>
      ))}
      {rows.map((e) => {
        const a = AGENT[e.agent ?? e.to ?? ""] ?? AGENT.scribe;
        const dot = e.type === "verified" ? "#30a46c" : e.type === "awaiting_approval" ? "#6e56cf" : e.type === "error" ? "#e5484d" : a.color;
        const x = e.args ?? {}, subj = x.entity_id ?? x.node_id ?? x.root_entity_id, what: string = TOOL_LABEL[e.tool] ?? e.tool;
        return (
          <li key={e.seq} className="fade-in grid grid-cols-[56px_minmax(0,1fr)] gap-x-4">
            <div className="mono pt-[5px] text-right text-[11.5px] text-ink-3 num">+{(e.t - t0).toFixed(1)}s</div>
            <div className="relative border-l border-line pb-3.5 pl-5">
              <span className="absolute -left-[4.5px] top-[9px] h-2 w-2 rounded-full ring-[3px] ring-panel" style={{ background: dot }} />
              {e.type === "phase" && <div className="pt-0.5 text-[13px] font-medium">{AGENT[e.agent]?.label ?? sentence(e.agent)} <span className="font-normal text-ink-2">began</span></div>}
              {e.type === "tool_call" && (
                <div className="pt-0.5 text-[13px]">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{a.label}</span>
                    <span className="text-ink-2">
                      {e.tool === "graph_explain_connection" && x.from_id ? <>{what} <span className="mono text-[12px]">{pretty(x.from_id)} → {pretty(x.to_id)}</span></>
                        : subj ? (what.endsWith(" it") ? <>{what.slice(0, -2)}<span className="mono text-[12px]">{pretty(subj)}</span></> : <>{what} for <span className="mono text-[12px]">{pretty(subj)}</span></>)
                        : what}
                    </span>
                    {e.n > 1 && <span className="text-[12px] font-medium text-ink-2 num">×{e.n}</span>}
                    <span className="mono text-[11.5px] text-ink-3">{e.tool} · {Math.round(e.ms)}ms{e.evidence_ids?.length ? ` · ${e.evidence_ids.length} evidence` : ""}</span>
                    {e.error && (e.tool === "graph_cypher_readonly"
                      ? <span className="cursor-help text-[12px] text-[#a35200]" title={String(e.error)}>FalkorDB rejected this query; the agent retried</span>
                      : <span className="text-[12px] text-[#ce2c31]" title={String(e.error)}>{String(e.error).slice(0, 80)}</span>)}
                  </div>
                  {x.purpose && <div className="mt-0.5 text-[12.5px] text-ink-2">{clean(x.purpose)}</div>}
                  {x.cypher && <pre className="mono mt-1 whitespace-pre-wrap break-words rounded-md border border-line bg-sunken px-2.5 py-1.5 text-[11.5px] leading-5">{x.cypher}</pre>}
                  {CYPHER[e.tool] && (
                    <details className="mt-0.5">
                      <summary className="w-fit cursor-pointer select-none text-[11.5px] text-ink-3 hover:text-ink-2">Cypher (shape)</summary>
                      <pre className="mono mt-1 whitespace-pre-wrap break-words rounded-md border border-line bg-sunken px-2.5 py-1.5 text-[11.5px] leading-5 text-ink-2">{CYPHER[e.tool]}</pre>
                    </details>
                  )}
                </div>
              )}
              {e.type === "handoff" && (
                <div className="mt-0.5 rounded-md border border-line bg-sunken px-3 py-2 text-[12.5px]">
                  <div className="font-medium">Handoff: {AGENT[e.from]?.label ?? e.from} → {AGENT[e.to]?.label ?? e.to}</div>
                  <div className="mt-0.5 leading-relaxed text-ink-2">{clean(e.summary)}</div>
                  {e.open_questions?.length > 0 && <div className="mt-1 text-ink-3">Open questions: {e.open_questions.join(" · ")}</div>}
                </div>
              )}
              {e.type === "verified" && (
                <div className="pt-0.5 text-[13px]"><span className="inline-flex items-center gap-1.5 font-medium text-[#18794e]"><ShieldCheck size={13} />Verifier</span> <span className="text-ink-2">checked {e.n_claims} claims against real graph edges: {e.n_verified} verified, path validity {Math.round(e.path_validity * 100)}%. No LLM involved.</span></div>
              )}
              {e.type === "awaiting_approval" && <div className="pt-0.5 text-[13px]"><span className="font-medium">Policy gate</span> <span className="text-ink-2">held the page for a human to approve</span></div>}
              {e.type === "closed" && <div className="pt-0.5 text-[13px]"><span className="font-medium">Scribe</span> <span className="text-ink-2">closed the incident and consolidated memory</span></div>}
              {e.type === "error" && <div className="pt-0.5 text-[13px]"><span className="font-medium text-[#ce2c31]">Run failed</span> <span className="text-ink-2">{e.error}</span></div>}
            </div>
          </li>
        );
      })}
      {busy && (
        <li className="grid grid-cols-[56px_minmax(0,1fr)] gap-x-4"><span />
          <div className="flex items-center gap-2 pl-5 text-[12.5px] text-ink-3"><Loader2 size={13} className="animate-spin" />{!rows.length ? "Correlating alerts and starting triage…" : reviewing ? "Investigator is reviewing the evidence with Gemini…" : "Working…"}</div>
        </li>
      )}
    </ol>
  );
}

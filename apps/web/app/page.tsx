"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ALERT_COLOR, API, ROOT_COLOR, TYPE_COLOR, ago, arrow, fmtTime, get, post, sentence } from "@/lib/api";
import { AGENT, Button, Menu, Meter, Note, PageBar, Person, Prop, Props, Section, Sev, Status, TOOL_LABEL, Tabs, Tag } from "@/components/ui";
import CausalChain, { type CEdge, type CNode } from "@/components/CausalChain";
import NetGraph, { type NEdge, type NNode } from "@/components/NetGraph";
import { AlertTriangle, BellRing, Check, ChevronDown, Copy, Loader2, Play, RotateCcw, ShieldCheck, Sparkles, Ticket, ThumbsDown, ThumbsUp, Undo2, Wrench, X } from "lucide-react";

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
const STEPS = ["Triage", "Investigate", "Verify", "Respond", "Approve", "Learn"];
const nameOf = (id: string) => id.replace(/^(svc_|ds_)/, "");
const cfg = (s?: string) => (s?.includes(": ") ? s.split(": ").slice(1).join(": ") : s || "");
const pretty = (s?: string) => arrow((s ?? "").replace(/\b(svc|ds)_([a-z0-9-]+)/g, "$2"));
const fault = (f?: string) => ({ cert_expiry: "certificate expired" } as Record<string, string>)[f ?? ""] ?? String(f || "unknown fault").replaceAll("_", " ");

export default function Incidents() {
  const [heroes, setHeroes] = useState<any[]>([]);
  const [ov, setOv] = useState<any>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [inc, setInc] = useState<any>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [detail, setDetail] = useState<any>(null);
  const [graph, setGraph] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "graph" | "timeline">("timeline");
  const [now, setNow] = useState(Date.now());
  const [fb, setFb] = useState<string | null>(null);
  const es = useRef<EventSource | null>(null);

  const loadOv = useCallback(() => get("/overview").then(setOv).catch(() => {}), []);
  useEffect(() => { get("/scenarios").then((s) => setHeroes(s.hero)).catch((e) => setErr(String(e))); loadOv(); }, [loadOv]);
  useEffect(() => { if (!busy) return; const i = setInterval(() => setNow(Date.now()), 200); return () => clearInterval(i); }, [busy]);

  const refresh = useCallback(async (id: string) => {
    const [d, g] = await Promise.all([get(`/incidents/${id}`), get(`/graph/incident/${id}`)]);
    setDetail(d); setGraph(g); setTab((t) => (t === "timeline" ? "overview" : t));
  }, []);

  const run = async (sid: string) => {
    setErr(null); setBusy(true); setEvents([]); setDetail(null); setGraph(null); setSel(sid); setFb(null); setTab("timeline");
    es.current?.close();
    document.getElementById("panel")?.scrollTo({ top: 0 });
    try {
      const r = await post("/sim/play", { kind: "hero", sid });
      setInc({ ...r, startedAt: Date.now() });
      const src = new EventSource(`${API}/incidents/${r.incident_id}/stream`);
      es.current = src;
      const on = (e: MessageEvent) => {
        if (!e.data) return;
        const ev = JSON.parse(e.data);
        setEvents((xs) => (xs.some((x) => x.seq === ev.seq) ? xs : [...xs, ev]));
        if (ev.type === "result" || ev.type === "error") { refresh(r.incident_id); setBusy(false); }
      };
      ["start", "phase", "tool_call", "handoff", "verified", "action", "awaiting_approval", "closed", "result", "error"].forEach((t) => src.addEventListener(t, on));
      src.onerror = () => src.close();
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };
  const back = () => { es.current?.close(); setInc(null); setEvents([]); setDetail(null); setGraph(null); setSel(null); loadOv(); };
  const reset = async () => { setBusy(true); try { await post("/admin/reset"); back(); } finally { setBusy(false); } };
  const decide = async (id: string, d: "approve" | "reject") => { await post(`/actions/${id}/${d}`); await refresh(inc.incident_id); };
  const feedback = async (correct: boolean) => {
    await post(`/incidents/${inc.incident_id}/feedback`, { correct });
    setFb(correct ? "Recorded. The suspected cause is now a confirmed cause and the lesson was upvoted." : "Recorded. The lesson was downvoted and the hypothesis marked refuted.");
    await refresh(inc.incident_id);
  };

  const result = detail?.run?.result;
  const tri = result?.triage, inv = result?.investigation, ver = result?.verification, resp = result?.response;
  const root = inv?.root;
  const status: string = detail?.run?.status ?? (inc ? "investigating" : "idle");
  const calls = events.filter((e) => e.type === "tool_call");
  const step = (i: number): "done" | "active" | "todo" => {
    const has = (t: string, f?: (e: Ev) => boolean) => events.some((e) => e.type === t && (!f || f(e)));
    const done = [has("handoff", (e) => e.from === "triage"), has("verified"), has("verified"), has("result") || has("awaiting_approval"), status === "closed", has("closed")];
    const started = [has("phase", (e) => e.phase === "triage"), has("phase", (e) => e.phase === "investigate"), has("verified"), has("phase", (e) => e.phase === "respond"), status === "awaiting_approval" || status === "closed", status === "closed"];
    return done[i] ? "done" : started[i] ? "active" : "todo";
  };
  const elapsed = result?.elapsed_s ? `${result.elapsed_s.toFixed(1)}s` : inc ? `${((now - inc.startedAt) / 1000).toFixed(1)}s` : "";

  const chain = useMemo(() => {
    if (!root || !inv || !inc) return null;
    const nodes: CNode[] = [], edges: CEdge[] = [];
    const path: string[] = root.path?.length ? root.path : [root.entity_id];
    const k = path.length;
    nodes.push({ id: "inc", label: inc.key, sub: sentence(inc.intake?.title), kind: "incident", col: 0, row: 0 });
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
  }, [root, inv, inc, graph, resp]);

  const ev = useMemo(() => {
    if (!graph || !inc) return null;
    const al = new Set<string>(graph.alerting ?? []), path = new Set<string>(root?.path ?? []);
    const rootIds = new Set([root?.change_id, root?.entity_id].filter(Boolean));
    const nodes: NNode[] = graph.nodes.map((n: any) => {
      const isRoot = rootIds.has(n.id), comp = n.type === "Component", ds = n.id.startsWith("ds_");
      return {
        id: n.id, type: comp ? (ds ? "Datastore" : "Service") : n.type,
        label: n.type === "Change" ? arrow(n.label) : n.label, detail: arrow(n.label),
        layer: n.id === inc.incident_id ? 0 : comp ? (al.has(n.id) && !isRoot ? 1 : 2) : n.type === "Change" ? 3 : n.type === "Incident" ? 4 : 5,
        color: isRoot ? ROOT_COLOR : comp && al.has(n.id) ? ALERT_COLOR : TYPE_COLOR[comp ? (ds ? "Datastore" : "Service") : n.type] ?? "#8b8d98",
        ring: isRoot ? "#f6c4c6" : n.id === inc.incident_id ? "#f6dcc3" : undefined,
        square: n.type === "Change" || n.type === "Incident", strong: isRoot || n.id === inc.incident_id || path.has(n.id),
      };
    });
    const edges: NEdge[] = graph.edges.map((e: any) => ({
      ...e, faint: e.type === "AFFECTED",
      hot: (e.type === "DEPENDS_ON" && path.has(e.source) && path.has(e.target)) || (e.type === "CHANGED" && e.source === root?.change_id),
    }));
    return { nodes, edges };
  }, [graph, inc, root]);

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
          <Button variant="ghost" icon={<RotateCcw size={13} />} onClick={reset} disabled={busy}>Reset demo</Button>
          <Menu width={360} trigger={(o, t) => <Button variant="primary" onClick={t} disabled={busy || !heroes.length} icon={<Play size={12} fill="currentColor" />}>Simulate incident<ChevronDown size={13} className="-mr-0.5 opacity-60" /></Button>}>
            {(close) => heroes.map((h) => (
              <button key={h.id} onClick={() => { close(); run(h.id); }} className="flex w-full gap-3 rounded-md px-2.5 py-2 text-left hover:bg-hover">
                <span className="mono mt-px w-5 text-[11.5px] text-ink-3">{h.id}</span>
                <span className="min-w-0"><span className="block text-[13px] font-medium">{HERO[h.id]?.title}</span><span className="block text-[12px] leading-snug text-ink-3">{HERO[h.id]?.tests}</span></span>
              </button>
            ))}
          </Menu>
        </>} />
        <div className="max-w-[1240px] px-6 py-6">
          {err && <div className="mb-5"><Note tone="red">{err}. Is the API running on {API}?</Note></div>}
          <div className="box grid grid-cols-2 overflow-hidden sm:grid-cols-3 xl:grid-cols-6">
            {stats.map(([l, v], i) => (
              <div key={l} className={`px-4 py-3 ${i ? "border-l border-line" : ""}`}>
                <div className="truncate text-[12px] text-ink-3">{l}</div>
                <div className="mt-0.5 text-[17px] font-semibold tracking-[-0.01em] num">{v}</div>
              </div>
            ))}
          </div>

          <Section title="Demo scenarios" count={heroes.length || undefined} className="mt-8" right="Each run replays alerts and changes into a fresh copy of the graph">
            <div className="box divide-y divide-line">
              {heroes.map((h) => (
                <div key={h.id} className="row flex items-center gap-4 px-4 py-3">
                  <span className="mono w-6 text-[12px] text-ink-3">{h.id}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium">{HERO[h.id]?.title}</div>
                    <div className="truncate text-[12.5px] text-ink-2">{HERO[h.id]?.tests}</div>
                  </div>
                  <div className="hidden gap-1 lg:flex">{HERO[h.id]?.tracks.map((t) => <Tag key={t}>{t}</Tag>)}</div>
                  <Button disabled={busy} onClick={() => run(h.id)} icon={<Play size={11} fill="currentColor" />}>Run</Button>
                </div>
              ))}
              {!heroes.length && <div className="p-4"><div className="skeleton h-10" /></div>}
            </div>
          </Section>

          <Section title="All incidents" count={c.Incident?.toLocaleString()} className="mt-8" right={`showing latest ${ov?.recent_incidents?.length ?? 0}`}>
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-[12px] text-ink-3">
                  <th className="h-8 w-[120px] pl-2 font-normal">ID</th><th className="font-normal">Title</th>
                  <th className="w-[96px] font-normal">Severity</th><th className="w-[128px] font-normal">Status</th><th className="w-[96px] pr-2 text-right font-normal">Started</th>
                </tr>
              </thead>
              <tbody>
                {(ov?.recent_incidents ?? []).map((i: any) => (
                  <tr key={i.id} className="row h-10 border-b border-line">
                    <td className="mono pl-2 text-[12.5px] text-ink-2">{i.key}</td>
                    <td className="pr-4"><span className="line-clamp-1">{sentence(i.title)}</span></td>
                    <td><Sev level={i.sev} /></td>
                    <td><Status s={i.status} /></td>
                    <td className="pr-2 text-right text-ink-3 num" title={fmtTime(i.started_at)}>{ago(i.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </div>
      </>
    );
  }

  /* ---------------- live incident ---------------- */
  const departed = resp?.departed_experts?.[0];
  return (
    <>
      <PageBar crumbs={[{ label: "Incidents", onClick: back }, inc.key]} actions={<>
        <select value={sel ?? ""} disabled={busy} onChange={(e) => run(e.target.value)}
          className="h-7 rounded-md border border-line-2 bg-panel px-2 text-[12.5px] shadow-[0_1px_1px_rgba(0,0,0,0.03)] outline-none">
          {heroes.map((h) => <option key={h.id} value={h.id}>{h.id} · {HERO[h.id]?.title}</option>)}
        </select>
        <Button variant="ghost" icon={<RotateCcw size={13} />} onClick={reset} disabled={busy}>Reset demo</Button>
      </>} />
      <div className={`grid min-h-[calc(100%-48px)] ${tab === "graph" ? "" : "xl:grid-cols-[minmax(0,1fr)_340px]"}`}>
        <div className="min-w-0 px-8 pb-12 pt-6">
          <div className="flex flex-wrap items-center gap-x-2 text-[12.5px] text-ink-3">
            <span className="mono">{inc.key}</span><span>·</span><span>Detected {fmtTime(inc.intake?.detected_at)}</span><span>·</span>
            <span>{inc.intake?.n_alerts} alerts across {inc.intake?.alerting?.length} services, {inc.intake?.noise_dropped} noise dropped</span><span>·</span>
            <span className="num"><span className="text-ink-2">{calls.length}</span> MCP tool calls in <span className="text-ink-2">{elapsed}</span></span>
          </div>
          <h1 className="mt-1.5 text-[22px] font-semibold tracking-[-0.015em]">{sentence(inc.intake?.title)}</h1>

          <div className="mt-4 flex items-center rounded-lg border border-line px-3.5 py-2.5">
            <ol className="flex flex-wrap items-center gap-y-1">
              {STEPS.map((s, i) => {
                const st = step(i), human = i === 4 && st === "active";
                return (
                  <li key={s} className="flex items-center">
                    <span className={`flex items-center gap-1.5 text-[12.5px] ${st === "todo" ? "text-ink-3" : st === "active" ? "font-medium text-ink" : "text-ink-2"}`}>
                      {st === "done" ? <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-green"><Check size={9} strokeWidth={3.5} className="text-white" /></span>
                        : human ? <i className="pulse h-2 w-2 rounded-full bg-brand text-brand" />
                        : st === "active" ? <Loader2 size={14} className="animate-spin text-brand" />
                        : <span className="h-3.5 w-3.5 rounded-full border border-dashed border-ink-3" />}
                      {human ? "Waiting for approval" : s}
                    </span>
                    {i < STEPS.length - 1 && <span className="mx-3 h-px w-6 bg-line-2" />}
                  </li>
                );
              })}
            </ol>
          </div>

          <div className="mt-6">
            <Tabs value={tab} onChange={setTab} tabs={[
              { key: "overview", label: "Overview", disabled: !result },
              { key: "graph", label: "Evidence graph", disabled: !ev, count: ev?.nodes.length },
              { key: "timeline", label: "Timeline", count: calls.length },
            ]} />
          </div>

          <div className="pt-6">
            {tab === "overview" && (!root ? <div className="space-y-3"><div className="skeleton h-28" /><div className="skeleton h-64" /></div> : (
              <div className="fade-in space-y-9">
                <RootCause inv={inv} root={root} ver={ver} />
                {chain && (
                  <Section title="How it connects" right={<span className="flex items-center gap-3"><span className="flex items-center gap-1.5"><i className="h-0.5 w-3.5 bg-red" />ranked path</span><span className="flex items-center gap-1.5"><i className="h-0 w-3.5 border-t border-dashed border-[#8f7fe0]" />memory / expertise</span></span>}>
                    <div className="box overflow-x-auto bg-sunken p-4"><CausalChain nodes={chain.nodes} edges={chain.edges} rowLabels={["Cause", "Ownership", "Memory"]} /></div>
                  </Section>
                )}
                <Claims inv={inv} ver={ver} />
                {resp?.status_update && (
                  <Section title="Status update" right={<Button variant="ghost" icon={<Copy size={12} />} onClick={() => navigator.clipboard?.writeText(pretty(resp.status_update))}>Copy</Button>}>
                    <div className="box px-4 py-3 text-[13px] leading-6 text-ink-2">{pretty(resp.status_update)}</div>
                  </Section>
                )}
                {inv.candidates?.length > 1 && (
                  <Section title="Other suspects" count={Math.min(5, inv.candidates.length - 1)} right="ranked lower by the same scoring">
                    <table className="w-full text-[13px]">
                      <thead><tr className="border-b border-line text-left text-[12px] text-ink-3"><th className="h-8 w-[160px] font-normal">Component</th><th className="font-normal">Change</th><th className="w-[100px] font-normal">Landed</th><th className="w-[140px] text-right font-normal">Score</th></tr></thead>
                      <tbody>{inv.candidates.slice(1, 6).map((c: any) => (
                        <tr key={c.change_id ?? c.entity_id} className="h-10 border-b border-line">
                          <td className="mono text-[12.5px]">{c.entity_name}</td><td className="pr-4"><span className="line-clamp-1 text-ink-2">{arrow(cfg(c.change_summary))}</span></td>
                          <td className="text-ink-3 num">{c.minutes_before_detection != null ? `${c.minutes_before_detection}m before` : "—"}</td>
                          <td><div className="flex items-center justify-end gap-2.5"><Meter value={c.score} color="#b4b4ae" width={72} /><span className="mono w-8 text-right text-ink-2 num">{c.score.toFixed(2)}</span></div></td>
                        </tr>))}</tbody>
                    </table>
                  </Section>
                )}
              </div>
            ))}
            {tab === "graph" && ev && (
              <div className="fade-in">
                <NetGraph nodes={ev.nodes} edges={ev.edges}
                  height={Math.max(440, Math.min(660, 80 + 26 * Math.max(...[0, 1, 2, 3, 4, 5].map((l) => ev.nodes.filter((n) => n.layer === l).length))))}
                  lanes={{ 0: "Incident", 1: "Alerting", 2: "Dependencies", 3: "Changes", 4: "Past incidents", 5: "People, runbooks" }} />
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-ink-2">
                  {([["Root cause", ROOT_COLOR], ["Alerting", ALERT_COLOR], ["Service", TYPE_COLOR.Service], ["Datastore", TYPE_COLOR.Datastore], ["Change", TYPE_COLOR.Change, 1], ["Incident", TYPE_COLOR.Incident, 1], ["Person", TYPE_COLOR.Person], ["Runbook", TYPE_COLOR.Runbook]] as [string, string, number?][]).map(([l, c, sq]) => (
                    <span key={l} className="flex items-center gap-1.5"><i className={`h-2 w-2 ${sq ? "rounded-[2px]" : "rounded-full"}`} style={{ background: c }} />{l}</span>
                  ))}
                  <span className="ml-auto text-ink-3">{ev.nodes.length} nodes and {ev.edges.length} relationships returned by the agents' MCP tools</span>
                </div>
              </div>
            )}
            {tab === "timeline" && <Timeline events={events} busy={busy} />}
          </div>
        </div>

        <aside className={`space-y-8 border-t border-line bg-sunken px-6 pb-10 pt-6 xl:border-l xl:border-t-0 ${tab === "graph" ? "hidden" : ""}`}>
          <Section title="Properties">
            <Props>
              <Prop k="Status"><Status s={status} /></Prop>
              <Prop k="Severity">{tri ? <Sev level={tri.severity} /> : <Pending />}</Prop>
              <Prop k="Root cause">{root ? <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-red" /><span className="mono text-[12.5px]">{root.entity_name}</span></span> : <Pending />}</Prop>
              {root?.change_id && <Prop k="Change"><span className="mono text-[12.5px]">{root.change_id}</span></Prop>}
              <Prop k="Owner">{resp ? resp.owner?.team ?? "—" : <Pending />}</Prop>
              <Prop k="On call">{resp ? <Person name={resp.oncall?.name} /> : <Pending />}</Prop>
              <Prop k="Top expert">{resp ? <Person name={resp.experts?.[0]?.name} /> : <Pending />}</Prop>
              <Prop k="Runbook">{inv ? (inv.runbook ? <span className="flex items-center gap-2"><span className="mono text-[12.5px]">{inv.runbook.key}</span><Tag tone={inv.runbook.stale ? "amber" : "green"}>{inv.runbook.stale ? "Stale" : "Verified"}</Tag></span> : "—") : <Pending />}</Prop>
              <Prop k="Entry points">{tri ? <span className="mono text-[12.5px]">{tri.entry_points?.join(", ") || "—"}</span> : <Pending />}</Prop>
            </Props>
            {resp && (resp.bus_factor <= 1 || departed) && (
              <div className="mt-3"><Note icon={<AlertTriangle size={13} />}>Bus factor {resp.bus_factor}.{departed ? ` ${departed.name}, a top expert on this, has left the company.` : ""}</Note></div>
            )}
          </Section>

          <Section title="Actions" right="gated by policy">
            {!detail?.actions?.length ? <div className="skeleton h-28" /> : (
              <div className="box divide-y divide-line">
                {detail.actions.map((a: any) => <ActionRow key={a.id} a={a} decide={decide} />)}
              </div>
            )}
          </Section>

          {inv && (
            <Section title="Similar incidents" count={inv.similar?.length || undefined}>
              {inv.similar?.length ? (
                <div className="-mx-2">
                  {inv.similar.map((s: any) => (
                    <div key={s.incident_id} className="row rounded-md px-2 py-2">
                      <div className="flex items-center justify-between text-[12.5px]"><span className="mono font-medium">{s.key}</span><span className="text-ink-3 num">{Math.round(s.similarity * 100)}% match</span></div>
                      <div className="truncate text-[12.5px] text-ink-2">{sentence(s.title)}</div>
                      <div className="mt-1 flex items-center gap-3 text-[12px] text-ink-3">
                        {s.fixed_by?.[0] && <Person name={s.fixed_by[0].name} size={16} dim={s.fixed_by[0].active === false} sub={s.fixed_by[0].active === false ? "left" : undefined} />}
                        {s.runbooks?.[0] && <span className="mono">{s.runbooks[0].key}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              ) : <p className="text-[12.5px] text-ink-3">No close match in memory.</p>}
            </Section>
          )}

          {status === "closed" && (
            <Section title="Was the diagnosis right?">
              <div className="flex gap-2">
                <Button className="flex-1" icon={<ThumbsUp size={13} />} onClick={() => feedback(true)}>Correct</Button>
                <Button className="flex-1" icon={<ThumbsDown size={13} />} onClick={() => feedback(false)}>Wrong</Button>
              </div>
              <p className="mt-2 text-[12px] leading-snug text-ink-3">{fb ?? "Your answer is written to the graph and changes what the crew learns."}</p>
            </Section>
          )}
        </aside>
      </div>
    </>
  );
}

const Pending = () => <span className="skeleton inline-block h-3.5 w-24 align-middle" />;

function RootCause({ inv, root, ver }: any) {
  const change = root.type === "change";
  const c0 = inv.candidates?.find((c: any) => c.change_id && c.change_id === root.change_id);
  const factors = Object.entries(root.breakdown ?? {}).filter(([k]) => k in TERMS) as [string, number][];
  const conf = inv.confidence ?? root.score;
  return (
    <div className="box overflow-hidden">
      <div className="flex gap-6 p-5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
            <span className="inline-flex items-center gap-1.5 font-medium text-[#ce2c31]"><i className="h-1.5 w-1.5 rounded-full bg-red" />Root cause</span>
            <span>·</span><span>{change ? "Change-induced" : "Not caused by a change"}</span>
            {inv.contested ? <Tag tone="amber"><Sparkles size={11} />Gemini disagrees, needs review</Tag> : inv.llm?.decision ? <Tag tone="violet"><Sparkles size={11} />Gemini review agrees</Tag> : null}
          </div>
          <div className="mt-2 text-[18px] font-semibold leading-snug tracking-[-0.01em]">
            {change ? <>{arrow(cfg(root.summary))} <span className="font-normal text-ink-3">on</span> {root.entity_name}</> : <>{sentence(fault(root.fault_type))} <span className="font-normal text-ink-3">on</span> {root.entity_name}</>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[12.5px] text-ink-2">
            {change && <span className="mono">{root.change_id}</span>}
            {c0?.minutes_before_detection != null && <><span className="text-ink-3">·</span><span>landed {c0.minutes_before_detection} min before detection</span></>}
            {!change && <span>No change in the window scored above the threshold, so the crew did not blame a deploy.</span>}
            {root.path?.length > 1 && <><span className="text-ink-3">·</span><span>impact path {root.path.map(nameOf).join(" → ")}</span></>}
          </div>
        </div>
        <div className="shrink-0 text-right">
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

function Claims({ inv, ver }: any) {
  return (
    <Section title="Claims" count={ver ? `${ver.n_verified} of ${ver.n_claims} verified` : undefined} right="each claim cites tool evidence; code re-checks every edge">
      <div className="box divide-y divide-line">
        {inv.claims.map((c: any, i: number) => (
          <div key={i} className="flex gap-3 px-4 py-3">
            {c.verified
              ? <span className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#e3f5eb]"><Check size={10} strokeWidth={3} className="text-[#18794e]" /></span>
              : <span className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#fde8e8]"><X size={10} strokeWidth={3} className="text-[#ce2c31]" /></span>}
            <div className="min-w-0 flex-1 text-[13px] leading-relaxed">
              <div className={c.verified ? "text-ink" : "text-ink-2"}>{pretty(c.text)}</div>
              {!c.verified && c.reasons?.length > 0 && <div className="mt-0.5 text-[12px] text-[#ce2c31]">Rejected by the verifier: {c.reasons.join("; ")}</div>}
              <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11.5px] text-ink-3">
                <span className="inline-flex items-center gap-1">{c.role === "llm_rationale" ? <><Sparkles size={10} />Gemini</> : "Graph ranker"}</span><span>·</span>
                <span className="mono truncate">{c.evidence_ids.join("  ")}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function ActionRow({ a, decide }: any) {
  const waiting = a.status === "awaiting_approval";
  const meta: Record<string, [string, any]> = { ticket: ["File a ticket", Ticket], page: ["Page on-call", BellRing], "remediation:rollback": ["Roll back the change", Undo2] };
  const [label, Icon] = meta[a.kind] ?? [sentence(a.kind.replace("remediation:", "").replaceAll("_", " ")), Wrench];
  const tone: Record<string, string> = { executed: "green", approved: "green", awaiting_approval: "violet", rejected: "red", proposed: "gray" };
  return (
    <div className={`px-3 py-2.5 ${waiting ? "bg-[#faf8ff]" : ""}`}>
      <div className="flex items-center gap-2.5">
        <Icon size={14} className="shrink-0 text-ink-3" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{label}</div>
          <div className="truncate text-[11.5px] text-ink-3">Tier {a.tier?.slice(1)} · <span className="mono">{nameOf(a.target)}</span></div>
        </div>
        <Tag tone={tone[a.status] ?? "gray"}>{waiting ? "Needs approval" : sentence(a.status.replaceAll("_", " "))}</Tag>
      </div>
      {waiting && (
        <div className="mt-2.5 flex gap-2 pl-6">
          <Button variant="primary" className="flex-1" onClick={() => decide(a.id, "approve")}>Approve</Button>
          <Button className="flex-1" onClick={() => decide(a.id, "reject")}>Reject</Button>
        </div>
      )}
    </div>
  );
}

function Timeline({ events, busy }: { events: Ev[]; busy: boolean }) {
  const t0 = events[0]?.t ?? 0;
  const rows = events.filter((e) => ["phase", "tool_call", "handoff", "verified", "awaiting_approval", "closed"].includes(e.type));
  if (!rows.length) return <div className="flex items-center gap-2 py-8 text-[13px] text-ink-2"><Loader2 size={14} className="animate-spin" /> Correlating alerts and starting triage…</div>;
  return (
    <ol className="max-w-[900px]">
      {rows.map((e) => {
        const a = AGENT[e.agent ?? e.to ?? ""] ?? AGENT.scribe;
        const dot = e.type === "verified" ? "#30a46c" : e.type === "awaiting_approval" ? "#6e56cf" : a.color;
        return (
          <li key={e.seq} className="fade-in grid grid-cols-[56px_minmax(0,1fr)] gap-x-4">
            <div className="mono pt-[5px] text-right text-[11.5px] text-ink-3 num">+{(e.t - t0).toFixed(1)}s</div>
            <div className="relative border-l border-line pb-3.5 pl-5">
              <span className="absolute -left-[4.5px] top-[9px] h-2 w-2 rounded-full ring-[3px] ring-panel" style={{ background: dot }} />
              {e.type === "phase" && <div className="pt-0.5 text-[13px] font-medium">{AGENT[e.agent]?.label ?? sentence(e.agent)} <span className="font-normal text-ink-2">started {e.phase}</span></div>}
              {e.type === "tool_call" && (
                <div className="flex flex-wrap items-baseline gap-x-2 pt-0.5 text-[13px]">
                  <span className="font-medium">{a.label}</span><span className="text-ink-2">{TOOL_LABEL[e.tool] ?? e.tool}</span>
                  <span className="mono text-[11.5px] text-ink-3">{e.tool} · {Math.round(e.ms)}ms{e.evidence_ids?.length ? ` · ${e.evidence_ids.length} evidence` : ""}</span>
                  {e.error && <span className="text-[12px] text-[#ce2c31]">{String(e.error).slice(0, 80)}</span>}
                </div>
              )}
              {e.type === "handoff" && (
                <div className="mt-0.5 rounded-md border border-line bg-sunken px-3 py-2 text-[12.5px]">
                  <div className="font-medium">Handoff: {AGENT[e.from]?.label ?? e.from} → {AGENT[e.to]?.label ?? e.to}</div>
                  <div className="mt-0.5 leading-relaxed text-ink-2">{pretty(e.summary)}</div>
                  {e.open_questions?.length > 0 && <div className="mt-1 text-ink-3">Open questions: {e.open_questions.join(" · ")}</div>}
                </div>
              )}
              {e.type === "verified" && (
                <div className="pt-0.5 text-[13px]"><span className="inline-flex items-center gap-1.5 font-medium text-[#18794e]"><ShieldCheck size={13} />Verifier</span> <span className="text-ink-2">checked {e.n_claims} claims against real graph edges: {e.n_verified} verified, path validity {Math.round(e.path_validity * 100)}%. No LLM involved.</span></div>
              )}
              {e.type === "awaiting_approval" && <div className="pt-0.5 text-[13px]"><span className="font-medium">Policy gate</span> <span className="text-ink-2">held the page for a human to approve</span></div>}
              {e.type === "closed" && <div className="pt-0.5 text-[13px]"><span className="font-medium">Scribe</span> <span className="text-ink-2">closed the incident and consolidated memory</span></div>}
            </div>
          </li>
        );
      })}
      {busy && <li className="grid grid-cols-[56px_minmax(0,1fr)] gap-x-4"><span /><div className="flex items-center gap-2 pl-5 text-[12.5px] text-ink-3"><Loader2 size={13} className="animate-spin" />Working…</div></li>}
    </ol>
  );
}

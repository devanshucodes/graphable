"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { TYPE_COLOR, ago, arrow, fmtDate, get, pretty, sentence } from "@/lib/api";
import { Meter, Note, PageBar, Person, Prop, Props, Section, Segmented, Sev, Tag } from "@/components/ui";
import NetGraph, { dagLayers, type NEdge, type NNode } from "@/components/NetGraph";
import OwnershipHistory from "@/components/OwnershipHistory";
import { AlertTriangle, Search } from "lucide-react";

/** Expertise signal types (org_experts) in plain words. */
const EVIDENCE: Record<string, string> = {
  fixer: "fixed incidents", postmortem: "postmortems", change: "changes", review: "reviews", ticket: "tickets", runbook: "runbooks",
  answer: "answers in chat", commander: "led incidents", helper: "helped on incidents", design: "design docs", wiki: "wiki pages",
};

export default function Explorer() {
  const [list, setList] = useState<any[]>([]);
  const [sel, setSel] = useState("svc_checkout");
  const [e, setE] = useState<any>(null);
  const [topo, setTopo] = useState<any>(null);
  const [view, setView] = useState<"entity" | "topology">("entity");
  const [qf, setQf] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    get("/entities").then(setList).catch((x) => setErr(x.message));
    get("/graph/topology").then(setTopo).catch((x) => setErr(x.message));
  }, []);
  useEffect(() => {
    let live = true; // clicking quickly through services must not let an older response overwrite a newer one
    setE(null);
    get(`/entities/${sel}`).then((x) => { if (live) setE(x); }).catch((x) => { if (live) setErr(x.message); });
    return () => { live = false; };
  }, [sel]);
  const open = useCallback((id: string) => { if (id?.startsWith("svc_") || id?.startsWith("ds_")) { setSel(id); setView("entity"); document.getElementById("panel")?.scrollTo({ top: 0 }); } }, []);
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    list.filter((c) => !qf || c.name.includes(qf.toLowerCase())).forEach((c) => (g[c.owner || "Unowned"] ??= []).push(c));
    return Object.entries(g).sort();
  }, [list, qf]);

  const map = useMemo(() => {
    if (!topo) return null;
    const L = dagLayers(topo.nodes.filter((n: any) => n.type !== "datastore").map((n: any) => n.id), topo.edges);
    const last = Math.max(0, ...L.values()) + 1;
    const nodes: NNode[] = topo.nodes.map((n: any) => ({
      id: n.id, label: n.label, type: n.type === "datastore" ? "Datastore" : "Service", layer: n.type === "datastore" ? last : L.get(n.id)!,
      color: n.type === "datastore" ? TYPE_COLOR.Datastore : TYPE_COLOR.Service, strong: n.tier === 0, detail: `${n.label} · tier ${n.tier} · owned by ${n.owner || "nobody"}`,
    }));
    const seen = new Set<string>(), edges: NEdge[] = []; // the topology lists a few pairs twice; keep one edge per direction
    for (const x of topo.edges) {
      const k = `${x.source}>${x.target}`;
      if (seen.has(k)) continue;
      seen.add(k); edges.push({ source: x.source, target: x.target, type: x.kind === "sync" ? "CALLS" : x.kind.toUpperCase() });
    }
    const lanes: Record<number, string> = { 0: "Entry points", [last]: "Datastores" };
    for (let i = 1; i < last; i++) lanes[i] = `Depth ${i}`;
    return { nodes, edges, lanes };
  }, [topo]);
  const hood = useMemo(() => {
    if (!map) return null;
    const by = new Map(map.nodes.map((n) => [n.id, n]));
    const callers = [...new Set(map.edges.filter((x) => x.target === sel && x.source !== sel).map((x) => x.source))];
    const allDeps = [...new Set(map.edges.filter((x) => x.source === sel && x.target !== sel).map((x) => x.target))];
    const deps = allDeps.filter((d) => !callers.includes(d)); // a 2-cycle (orders <-> inventory) is drawn once, in the callers lane
    const pick = (id: string, layer: number): NNode | null => (by.has(id) ? { ...by.get(id)!, layer, strong: id === sel, ring: id === sel ? "#d9d1fb" : undefined } : null);
    const nodes = [pick(sel, 1), ...callers.map((c) => pick(c, 0)), ...deps.map((d) => pick(d, 2))].filter(Boolean) as NNode[];
    return { nodes, edges: map.edges.filter((x) => x.target === sel || x.source === sel), callers: callers.length, deps: deps.length, both: allDeps.length - deps.length };
  }, [map, sel]);

  return (
    <>
      <PageBar crumbs={view === "entity" && e ? [{ label: "Services", onClick: () => setView("topology") }, e.entity.name] : ["Services"]}
        actions={<Segmented value={view} onChange={setView} options={[{ key: "entity", label: "Details" }, { key: "topology", label: "Dependency map" }]} />} />
      <div className="flex min-h-[calc(100%-48px)]">
        <aside className="hidden w-[244px] shrink-0 border-r border-line lg:block">
          <div className="sticky top-12 flex h-[calc(100dvh-66px)] flex-col">
            <div className="border-b border-line p-2">
              <div className="flex h-8 items-center gap-2 rounded-md bg-hover px-2.5 focus-within:ring-2 focus-within:ring-[#c4bbf0]">
                <Search size={13} className="text-ink-3" />
                <input value={qf} onChange={(x) => setQf(x.target.value)} placeholder="Filter services" aria-label="Filter services" className="w-full bg-transparent text-[13px] outline-none placeholder:text-ink-3" />
              </div>
            </div>
            <div className="scroll flex-1 overflow-y-auto px-2 pb-4">
              {groups.map(([team, cs]) => (
                <div key={team}>
                  <div className="flex items-center justify-between px-2 pb-1 pt-3.5 text-[12px] text-ink-3"><span>{team}</span><span className="num">{cs.length}</span></div>
                  {cs.map((c) => (
                    <button key={c.id} onClick={() => open(c.id)}
                      className={`flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] ${sel === c.id ? "bg-hover font-medium text-ink" : "text-ink-2 hover:bg-hover hover:text-ink"}`}>
                      <i className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: c.id.startsWith("ds_") ? TYPE_COLOR.Datastore : TYPE_COLOR.Service }} />
                      <span className="flex-1 truncate">{c.name}</span>{c.tier === 0 && <span className="text-[11px] text-ink-3">T0</span>}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          {err && <div className="px-6 pt-6"><Note tone="red">{err}</Note></div>}
          {view === "topology" ? (
            <div className="px-6 py-6">
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <div className="text-[13px] font-semibold">{map?.nodes.length ?? 0} components <span className="font-normal text-ink-3">· {map?.edges.length ?? 0} live dependencies</span></div>
                <div className="text-[12px] text-ink-3">Callers on top, what they call below. Bold = tier 0. Hover to trace, click to open.</div>
              </div>
              {map ? <NetGraph nodes={map.nodes} edges={map.edges} dir="tb" lanes={map.lanes} focus={sel} onNodeClick={open} /> : <div className="skeleton h-[640px]" />}
            </div>
          ) : !e ? <div className="space-y-3 px-8 py-6"><div className="skeleton h-20" /><div className="skeleton h-72" /></div> : (
            <div className="fade-in px-8 pb-12 pt-6">
              <div className="text-[12.5px] text-ink-3">{e.entity.kind === "datastore" ? `Datastore · ${e.entity.engine}` : `Service · ${e.entity.domain}`}</div>
              <h1 className="mt-1 text-[22px] font-semibold tracking-[-0.015em]">{e.entity.name}</h1>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                <Tag tone={e.entity.tier === 0 ? "red" : "gray"}>Tier {e.entity.tier}</Tag>
                {e.entity.language && <Tag>{e.entity.language}</Tag>}
                {e.entity.slo_availability && <Tag>SLO {e.entity.slo_availability}%</Tag>}
                {(e.entity.aliases ?? []).slice(0, 3).map((a: string) => <Tag key={a}>aka “{a}”</Tag>)}
              </div>

              <div className="mt-8 grid gap-10 xl:grid-cols-[minmax(0,1fr)_280px]">
                <div className="min-w-0 space-y-9">
                  {hood && hood.nodes.length > 1 && (
                    <Section title="Dependencies" right={`${hood.callers} callers · ${hood.deps} dependencies${hood.both ? ` · ${hood.both} both ways` : ""}`}>
                      <NetGraph nodes={hood.nodes} edges={hood.edges} height={Math.max(220, 100 + 28 * Math.max(hood.callers, hood.deps))} lanes={{ 0: "Called by", 1: "This service", 2: "Depends on" }} onNodeClick={open} />
                    </Section>
                  )}

                  <Section title="Who actually knows it" right="computed from fixes, postmortems, runbooks, tickets and changes">
                    <div className="box divide-y divide-line">
                      {e.experts.experts.map((x: any) => (
                        <div key={x.id} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_96px] items-center gap-4 px-4 py-2.5 text-[13px]">
                          <Person name={x.name} />
                          <span className="truncate text-[12.5px] text-ink-3">{[...new Set(x.top_evidence.map((t: any) => EVIDENCE[t.type] ?? String(t.type).replaceAll("_", " ")))].join(" · ")}</span>
                          <Meter value={x.score / (e.experts.experts[0]?.score || 1)} />
                        </div>
                      ))}
                    </div>
                    {e.experts.bus_factor <= 1 && (
                      <div className="mt-2.5"><Note icon={<AlertTriangle size={13} />}>Bus factor {e.experts.bus_factor}.{e.experts.departed_experts?.[0] ? ` ${e.experts.departed_experts[0].name} was a top expert and left on ${fmtDate(e.experts.departed_experts[0].left_at)}.` : ""}</Note></div>
                    )}
                  </Section>

                  <Section title="Recent changes" count={e.recent_changes.length}>
                    <table className="w-full text-[13px]">
                      <tbody>
                        {e.recent_changes.map((c: any) => (
                          <tr key={c.id} className="h-10 border-b border-line first:border-t">
                            <td className="w-[78px] text-ink-3 num" title={fmtDate(c.ts)}>{ago(c.ts)}</td>
                            <td className="w-[128px]"><Tag>{c.kind.replaceAll("_", " ")}</Tag></td>
                            <td className="pr-4"><span className="line-clamp-1">{arrow(c.summary.replace(`${e.entity.name}: `, "").replace(`${e.entity.name} `, ""))}</span></td>
                            <td className="w-[150px] text-right text-[12.5px] text-ink-2">{c.rolled_back ? <Tag tone="red">Rolled back</Tag> : c.author}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Section>

                  <Section title="Incidents" count={e.incidents.length}>
                    <table className="w-full text-[13px]">
                      <tbody>
                        {e.incidents.map((i: any) => (
                          <tr key={i.id} className="h-11 border-b border-line first:border-t">
                            <td className="mono w-[112px] text-[12.5px] text-ink-2">{i.key}</td>
                            <td className="w-[78px]"><Sev level={i.sev} /></td>
                            <td className="pr-4"><div className="line-clamp-1">{sentence(i.title)}</div>{i.cause?.[0] && <div className="line-clamp-1 text-[12px] text-ink-3">Cause: {pretty(i.cause[0])}</div>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Section>
                </div>

                <aside className="min-w-0 space-y-8">
                  <Section title="Properties">
                    <Props>
                      <Prop k="Owner">{e.ownership.owner?.team ?? "—"}</Prop>
                      <Prop k="On call"><div className="space-y-1 py-1">{e.ownership.oncall?.map((o: any) => <div key={o.id} title={o.level}><Person name={o.name} /></div>)}</div></Prop>
                      <Prop k="Blast radius">{e.blast_radius.n} services</Prop>
                      <Prop k="Bus factor" title="How many people would have to leave before nobody knows this service"><span className={e.experts.bus_factor <= 1 ? "text-[#a35200]" : ""}>{e.experts.bus_factor}</span></Prop>
                      <Prop k="Source">{e.ownership.owner?.source ?? "—"}</Prop>
                    </Props>
                  </Section>

                  <Section title="Ownership history" right="never deleted">
                    <OwnershipHistory history={e.ownership.history} />
                  </Section>

                  {e.knowledge.runbooks?.length > 0 && (
                    <Section title="Runbooks" count={e.knowledge.runbooks.length}>
                      <div className="space-y-2">
                        {e.knowledge.runbooks.map((r: any) => (
                          <div key={r.id} title={r.stale ? r.stale_reasons.join("; ") : undefined}>
                            <div className="flex items-center justify-between gap-2"><span className="mono text-[12.5px]">{r.key}</span><Tag tone={r.stale ? "amber" : r.status === "active" ? "green" : "gray"}>{r.stale ? "Stale" : sentence(r.status)}</Tag></div>
                            {r.stale && <div className="mt-0.5 text-[12px] leading-snug text-ink-3">{r.stale_reasons.join("; ")}</div>}
                          </div>
                        ))}
                      </div>
                    </Section>
                  )}

                  {e.knowledge.decisions?.length > 0 && (
                    <Section title="Decisions" count={e.knowledge.decisions.length}>
                      <div className="space-y-2.5">
                        {e.knowledge.decisions.map((d: any) => (
                          <div key={d.id}>
                            <div className="flex items-center justify-between gap-2"><span className="mono text-[12.5px]">{d.key}</span><Tag tone={d.status === "superseded" ? "gray" : "blue"}>{sentence(d.status)}</Tag></div>
                            <div className={`mt-0.5 text-[12.5px] leading-snug ${d.status === "superseded" ? "text-ink-3 line-through" : "text-ink-2"}`}>{d.title}</div>
                          </div>
                        ))}
                      </div>
                    </Section>
                  )}

                  {e.knowledge.lessons?.length > 0 && (
                    <Section title="Lessons">
                      {e.knowledge.lessons.map((l: any) => <div key={l.id} className="text-[12.5px] text-ink-2"><span className="font-medium text-ink">{l.votes} {Math.abs(l.votes) === 1 ? "vote" : "votes"}</span> · {pretty(l.text)}</div>)}
                    </Section>
                  )}
                </aside>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

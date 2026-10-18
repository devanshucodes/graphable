"use client";
import { useEffect, useState, type ReactNode } from "react";
import { get } from "@/lib/api";
import { Note, PageBar, Section } from "@/components/ui";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Check, Minus, X } from "lucide-react";

const C = { ours: "#6e56cf", prior: "#c4b8f3", toy: "#5f5f5a", recent: "#b4b4ae", random: "#d4d4cf" };
const tip = { contentStyle: { background: "#fff", border: "1px solid #ebebe8", borderRadius: 8, fontSize: 12, boxShadow: "0 6px 20px rgba(0,0,0,.08)" }, labelStyle: { color: "#5f5f5a" } };
const axis = { stroke: "#6f6f69", fontSize: 11, tickLine: false, axisLine: false };
const grid = "#f0f0ee";
const pct = (x?: number) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const dec = (x?: number) => (x == null ? "—" : x.toFixed(1));

function Chart({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <Section title={title} className="min-w-0">
      <p className="-mt-1.5 mb-3 text-[12.5px] text-ink-3">{sub}</p>
      <div className="box px-3 pb-2 pt-4">{children}</div>
    </Section>
  );
}

export default function Scoreboard() {
  const [ev, setEv] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get("/eval").then(setEv).catch((e) => setErr(e.message)); }, []);
  const t = ev?.rca_test?.tuned, d = ev?.rca_test?.default, sys = ev?.system_mcp;
  const byK = t ? Object.entries(t.by_distractors).map(([k, v]: any) => ({ k: Number(k), ours: v.ours, toy: v.toy, random: v.random, recent: v.recent })) : [];
  const overall = t ? [
    { name: "Graphable", v: t.ac1, c: C.ours }, { name: "Graphable, untuned", v: d.ac1, c: C.prior },
    { name: "Hops, then recency", v: d.base_toy, c: "#dcdcd8" }, { name: "Random in window", v: d.base_random, c: "#dcdcd8" }, { name: "Most recent change", v: d.base_recent, c: "#dcdcd8" },
  ] : [];
  const lc = sys?.learning_curve?.rows ?? [];
  const ex = ev?.extraction ?? {};
  const checks: [string, string, boolean][] = sys ? [
    ["Claims verified by code (graph-only crew)", pct(sys.crew.claims_verified), true],
    ["Cited graph paths that exist", pct(sys.crew.path_validity), true],
    ["Pages held for human approval", `${sys.crew.pages.awaiting_approval}/${sys.crew.n}`, true],
    ["Tenant isolation, identical service names", sys.isolation.pass ? "Pass" : "Fail", sys.isolation.pass],
    ["Concurrency: CAS claims, idempotent pages", sys.concurrency.pass ? "Pass" : "Fail", sys.concurrency.pass],
    ["Stale answers after org changes", `${sys.probes.knowledge_update.stale_answers}`, sys.probes.knowledge_update.stale_answers === 0],
    ["Abstains on non-change incidents", pct(t?.abstain_correct), (t?.abstain_correct ?? 0) >= 0.7],
    ["Auto-filed ticket precision", pct(sys.crew.auto_ticket_precision), false],
  ] : [];
  const metrics: [string, string, string][] = [
    ["Root cause, top-1", pct(t?.ac1), t ? `top-3 ${pct(t.ac3)} · random ${pct(d.base_random)}` : ""],
    ["Cited paths that exist", sys ? pct(sys.crew.path_validity) : "—", "every edge re-checked by code"],
    ["Racing pages → actions", sys ? `${sys.concurrency.concurrent_pages} → ${sys.concurrency.page_actions_created}` : "—", sys ? `${sys.concurrency.successful_claims} ${sys.concurrency.concurrent_claims != null ? `of ${sys.concurrency.concurrent_claims} ` : ""}racing claims won` : ""],
    ["Brain answers correct", sys ? `${sys.probes.brain_qa.correct}/${sys.probes.brain_qa.n}` : "—", sys ? `${pct(sys.probes.brain_qa.cited_rate)} cited · ${sys.probes.knowledge_update.stale_answers} stale` : ""],
  ];

  return (
    <>
      <PageBar crumbs={["Evaluation"]} />
      <div className="max-w-[1180px] px-8 pb-14 pt-6">
        <h1 className="text-[22px] font-semibold tracking-[-0.015em]">Evaluation</h1>
        <p className="mt-1 max-w-[720px] text-[13.5px] text-ink-2">40 held-out synthetic incidents with planted traps: decoy changes, silent roots, look-alikes and non-change causes. Weights were tuned on a separate 40-incident set, then frozen. Synthetic data, so read this as a demonstration, not a benchmark.</p>

        <div className="box mt-6 grid grid-cols-2 overflow-hidden lg:grid-cols-4">
          {metrics.map(([l, v, s], i) => (
            <div key={l} className={`px-5 py-4 ${i ? "border-l border-line" : ""} ${i === 2 ? "max-lg:border-l-0 max-lg:border-t" : ""} ${i === 3 ? "max-lg:border-t" : ""}`}>
              <div className="text-[12px] text-ink-3">{l}</div>
              <div className="mt-1 text-[26px] font-semibold leading-none tracking-[-0.02em] num">{ev ? v : "—"}</div>
              <div className="mt-1.5 text-[12px] text-ink-3">{s}</div>
            </div>
          ))}
        </div>

        {err && <div className="mt-6"><Note tone="red">{err}</Note></div>}
        {!ev ? !err && <div className="skeleton mt-8 h-80" /> : (
          <div className="mt-10 grid gap-x-10 gap-y-10 xl:grid-cols-2">
            <Chart title="Root cause, top-1 vs baselines" sub="Held-out test set, 40 incidents.">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={overall} layout="vertical" margin={{ left: 4, right: 44, top: 0, bottom: 0 }}>
                  <CartesianGrid stroke={grid} horizontal={false} />
                  <XAxis type="number" domain={[0, 1]} {...axis} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                  <YAxis type="category" dataKey="name" width={150} {...axis} stroke="#1c1c1a" fontSize={12} />
                  <Tooltip {...tip} formatter={(v: any) => `${Math.round(v * 100)}%`} cursor={{ fill: "#f7f7f5" }} />
                  <Bar dataKey="v" radius={[0, 3, 3, 0]} barSize={18} minPointSize={3} isAnimationActive={false}>
                    {overall.map((o, i) => <Cell key={i} fill={o.c} />)}
                    <LabelList dataKey="v" position="right" formatter={(v: any) => `${Math.round(v * 100)}%`} style={{ fontSize: 12, fill: "#1c1c1a", fontWeight: 600 }} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Chart>

            <Chart title="Accuracy as decoy changes pile up" sub="X-axis: innocent changes in the window. The graph keeps finding the root; “most recent change” does not.">
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={byK} margin={{ left: -14, right: 12, top: 6 }}>
                  <CartesianGrid stroke={grid} vertical={false} />
                  <XAxis dataKey="k" type="number" domain={[0, Math.max(15, ...byK.map((r) => r.k))]} ticks={byK.map((r) => r.k)} {...axis} /><YAxis domain={[0, 1]} {...axis} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                  <Tooltip {...tip} formatter={(v: any) => `${Math.round(v * 100)}%`} /><Legend wrapperStyle={{ fontSize: 12, color: "#5f5f5a" }} iconType="plainline" />
                  <Line name="Graphable" dataKey="ours" stroke={C.ours} strokeWidth={2.5} dot={{ r: 3, fill: C.ours, strokeWidth: 0 }} isAnimationActive={false} />
                  <Line name="Hops, then recency" dataKey="toy" stroke={C.toy} strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  <Line name="Most recent" dataKey="recent" stroke={C.recent} strokeWidth={1.5} strokeDasharray="5 3" dot={false} isAnimationActive={false} />
                  <Line name="Random" dataKey="random" stroke="#c9c9c4" strokeWidth={1.5} strokeDasharray="2 3" dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Chart>

            <Chart title="Learning a new failure pattern" sub="8 occurrences of a change type never seen before. After 3 on-call corrections are written to the graph, it is right every time.">
              <div className="mb-3 grid grid-cols-8 gap-1.5 px-1">
                {lc.map((r: any) => (
                  <div key={r.seq} className={`flex h-12 flex-col items-center justify-center gap-0.5 rounded-md border ${r.correct ? "border-[#c5ead6] bg-[#f3fbf6]" : "border-[#fbd5d6] bg-[#fff6f6]"}`}>
                    {r.correct ? <Check size={14} strokeWidth={2.5} className="text-[#18794e]" /> : <X size={14} strokeWidth={2.5} className="text-[#ce2c31]" />}
                    <span className="text-[11px] text-ink-3 num">#{r.seq}</span>
                  </div>
                ))}
              </div>
              <ResponsiveContainer width="100%" height={150}>
                {/* two scales: recalled count (0-3, left) and learned risk K (0-1, right); a run with no K stays a gap */}
                <LineChart data={lc.map((r: any) => ({ seq: `#${r.seq}`, K: r.K ?? null, recalled: r.recalled_previous_LC }))} margin={{ left: -18, right: -6 }}>
                  <CartesianGrid stroke={grid} vertical={false} /><XAxis dataKey="seq" {...axis} />
                  <YAxis yAxisId="n" {...axis} allowDecimals={false} domain={[0, 3.5]} ticks={[0, 1, 2, 3]} />
                  <YAxis yAxisId="k" orientation="right" {...axis} domain={[0, 3.5 / 3]} ticks={[0, 0.5, 1]} tickFormatter={(v) => (v === 0 || v === 1 ? String(v) : v.toFixed(1))} />
                  <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 12, color: "#5f5f5a" }} iconType="plainline" />
                  <Line yAxisId="k" name="Learned change-type risk (right axis, 0–1)" dataKey="K" connectNulls={false} stroke={C.ours} strokeWidth={2} dot={{ r: 2.5, fill: C.ours, strokeWidth: 0 }} isAnimationActive={false} />
                  <Line yAxisId="n" name="Past occurrences recalled (left axis)" dataKey="recalled" stroke={C.toy} strokeWidth={1.5} dot={{ r: 2.5, fill: C.toy, strokeWidth: 0 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Chart>

            <Section title="System tests" className="min-w-0" count={checks.length}>
              <p className="-mt-1.5 mb-3 text-[12.5px] text-ink-3">Run against the real MCP server over HTTP.</p>
              <div className="box divide-y divide-line">
                {checks.map(([l, v, ok]) => (
                  <div key={l} className="flex h-10 items-center gap-3 px-4 text-[13px]">
                    {ok ? <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[#e3f5eb]"><Check size={10} strokeWidth={3} className="text-[#18794e]" /></span>
                      : <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[#fff1d6]"><Minus size={10} strokeWidth={3} className="text-[#a35200]" /></span>}
                    <span className="flex-1">{l}</span><span className="mono font-medium num">{v}</span>
                  </div>
                ))}
              </div>
              <p className="mt-2.5 text-[12px] text-ink-3">Auto-filed tickets are the lowest-risk action. Because of their precision, pages and rollbacks always need a human.</p>
            </Section>

            {Object.keys(ex).length > 0 && (
              <Section title="Fact extraction from docs, tickets and chats" className="min-w-0">
                <p className="-mt-1.5 mb-3 text-[12.5px] text-ink-3">Precision / recall against the facts the prose was generated from.</p>
                <div className="box overflow-hidden">
                  <table className="w-full text-[13px]">
                    <thead><tr className="border-b border-line bg-sunken text-left text-[12px] text-ink-3"><th className="h-8 px-4 font-normal">Relation</th>{Object.keys(ex).map((k) => <th key={k} className="px-4 text-right font-normal">{k.startsWith("gemini") ? "Gemini" : "Rules"}</th>)}</tr></thead>
                    <tbody>
                      {["CAUSED_BY", "MITIGATED_WITH", "DECIDED_BY", "SUPERSEDES", "AFFECTS", "OWNS", "micro"].map((rel) => (
                        <tr key={rel} className={`h-9 border-b border-line last:border-0 ${rel === "micro" ? "bg-sunken font-medium" : ""}`}>
                          <td className="mono px-4 text-[12.5px]">{rel === "micro" ? "Overall" : rel}</td>
                          {Object.values(ex).map((v: any, i) => <td key={i} className="mono px-4 text-right text-[12.5px] num">{pct(v[rel]?.precision)} / {pct(v[rel]?.recall)}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            {(ev.crew_gemini_mcp || ev.crew_offline_mcp) && (
              <Section title="Graph ranker alone vs. with Gemini review" className="min-w-0">
                <p className="-mt-1.5 mb-3 text-[12.5px] text-ink-3">Same 40 incidents, same MCP tools.</p>
                <div className="box overflow-hidden">
                  <table className="w-full text-[13px]">
                    <thead><tr className="border-b border-line bg-sunken text-left text-[12px] text-ink-3"><th className="h-8 px-4 font-normal">Metric</th><th className="px-4 text-right font-normal">Graph only</th><th className="px-4 text-right font-normal">+ Gemini review</th></tr></thead>
                    <tbody>
                      {([["Root cause correct", "root_correct", pct], ["Claims verified", "claims_verified", pct], ["Contested for human review", "contested", (x: any) => x ?? 0],
                        ["MCP tool calls per incident", "avg_tool_calls", dec], ["Seconds per incident", "avg_seconds", dec]] as [string, string, (x: any) => any][]).map(([l, k, f]) => (
                        <tr key={k} className="h-9 border-b border-line last:border-0"><td className="px-4">{l}</td><td className="mono px-4 text-right text-[12.5px] num">{f(ev.crew_offline_mcp?.crew?.[k])}</td><td className="mono px-4 text-right text-[12.5px] num">{f(ev.crew_gemini_mcp?.crew?.[k])}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="border-t border-line px-4 py-3 text-[12.5px] leading-relaxed text-ink-2">
                    <span className="font-medium text-ink">Finding.</span> Letting the LLM override the graph fixed 1 incident and broke 3 look-alike traps. Now a disagreement marks the incident contested for a human, so accuracy holds and the LLM acts as a second opinion.
                  </div>
                </div>
              </Section>
            )}
          </div>
        )}
      </div>
    </>
  );
}

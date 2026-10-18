"use client";
import { useEffect, useMemo, useState } from "react";
import { clean, get } from "@/lib/api";
import { AGENT, Empty, Note, PageBar, Section, Tag } from "@/components/ui";
import { ArrowRight } from "lucide-react";

const REFLECTION = "Written when on-call marks a diagnosis wrong and names the actual cause.";

export default function MemoryPage() {
  const [m, setM] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get("/memory").then(setM).catch((e) => setErr(e.message)); }, []);
  const c = m?.counts ?? {};
  // handoffs grouped by incident; a summary already shown for another incident (a replayed scenario) collapses to a reference
  const groups = useMemo(() => {
    const out: { key: string; items: any[] }[] = [], first = new Map<string, string>();
    for (const h of m?.handoffs ?? []) {
      let g = out.find((x) => x.key === h.incident);
      if (!g) out.push((g = { key: h.incident, items: [] }));
      const sig = `${h.from}>${h.to}|${h.summary}`, dup = g.items.find((x) => x.sig === sig);
      if (dup) { dup.n++; continue; }
      g.items.push({ ...h, sig, n: 1, same: first.get(sig) });
      if (!first.has(sig)) first.set(sig, h.incident);
    }
    return out;
  }, [m]);
  const kinds = [
    { title: "Episodic", sub: "What happened", items: [["Agent runs", c.AgentRun], ["Tool steps", c.AgentStep], ["Evidence records", c.Evidence], ["Hypotheses", c.Hypothesis]] },
    { title: "Coordination", sub: "Shared state between agents", items: [["Handoffs", c.Handoff], ["Tasks", c.Task], ["Actions", c.Action], ["Brain answers", c.Answer]] },
    { title: "Procedural", sub: "How to fix things", items: [["Lessons", c.Lesson], ["Reflections", c.Reflection, REFLECTION], ["Proposed runbooks", m?.proposed_runbooks?.length]] },
  ];
  return (
    <>
      <PageBar crumbs={["Memory"]} />
      <div className="max-w-[1180px] px-8 pb-12 pt-6">
        <h1 className="text-[22px] font-semibold tracking-[-0.015em]">Agent memory</h1>
        <p className="mt-1 max-w-[680px] text-[13.5px] text-ink-2">Every agent reads and writes the same FalkorDB graph. Facts are closed when they stop being true, never deleted. Lessons are only promoted after a verified fix or a human confirmation.</p>

        {err && <div className="mt-5"><Note tone="red">{err}</Note></div>}
        <div className="box mt-6 grid overflow-hidden md:grid-cols-3">
          {kinds.map(({ title, sub, items }, i) => (
            <div key={title} className={`px-5 py-4 ${i ? "border-t border-line md:border-l md:border-t-0" : ""}`}>
              <div className="text-[13px] font-semibold">{title}</div>
              <div className="text-[12px] text-ink-3">{sub}</div>
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5">
                {items.map(([l, v, hint]: any) => <div key={l} className="flex flex-col-reverse" title={hint}><dt className={`text-[12px] text-ink-3 ${hint ? "w-fit cursor-help underline decoration-[#c9c9c4] decoration-dotted underline-offset-[3px]" : ""}`}>{l}</dt><dd className="text-[18px] font-semibold tracking-[-0.01em] num">{m ? v ?? 0 : "—"}</dd></div>)}
              </dl>
            </div>
          ))}
        </div>

        <div className="mt-10 grid gap-10 lg:grid-cols-2">
          <Section title="Lessons" className="min-w-0" count={m?.lessons?.length} right="+1 when a fix is confirmed, −1 when wrong">
            {!m ? <div className="skeleton h-40" /> : m.lessons.length === 0 ? <Empty title="No lessons yet" sub="Run an incident and confirm the diagnosis to create one." /> : (
              <div className="box divide-y divide-line">
                {m.lessons.map((l: any) => (
                  <div key={l.id} className="flex gap-4 px-4 py-3">
                    <div className="w-9 shrink-0 text-center"><div className="text-[16px] font-semibold num">{l.votes}</div><div className="text-[11px] text-ink-3">votes</div></div>
                    <div className="min-w-0 text-[13px]">
                      <div className="mb-1 flex gap-1.5"><Tag tone={l.status === "active" ? "green" : l.status === "retired" ? "red" : "gray"}>{l.status}</Tag>{l.component && <Tag>{l.component}</Tag>}</div>
                      <span className="text-ink-2">{clean(l.text)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {m?.reflections?.length > 0 && (
              <div className="mt-6">
                <div className="mb-2 text-[12px] text-ink-3"><span className="font-medium text-ink-2">Reflections</span> · {REFLECTION}</div>
                <div className="space-y-1.5">{m.reflections.map((r: any) => <div key={r.id} className="text-[12.5px] text-ink-2"><span className="mono text-ink">{r.component}</span> · {clean(r.text)}</div>)}</div>
              </div>
            )}
            {m?.proposed_runbooks?.length > 0 && (
              <div className="mt-6">
                <div className="mb-2 text-[12px] text-ink-3">Proposed runbooks, awaiting verification</div>
                {m.proposed_runbooks.map((r: any) => <pre key={r.id} className="mono mb-2 whitespace-pre-wrap rounded-md border border-line bg-sunken p-3 text-[12px] text-ink-2"><b className="text-ink">{r.key}</b>{"\n"}{r.text}</pre>)}
              </div>
            )}
          </Section>

          <Section title="Handoffs" className="min-w-0" count={m?.handoffs?.length} right="structured notes stored as graph nodes">
            {!m ? <div className="skeleton h-40" /> : m.handoffs.length === 0 ? <Empty title="No handoffs yet" sub="Simulate an incident to see agents hand work to each other." /> : (
              <div className="box divide-y divide-line">
                {groups.map((g) => (
                  <div key={g.key} className="pb-1">
                    <div className="mono px-4 pt-2.5 text-[11.5px] font-medium text-ink-3">{g.key}</div>
                    {g.items.map((h: any, i: number) => (
                      <div key={i} className="px-4 py-2">
                        <div className="mb-0.5 flex items-center gap-2 text-[12.5px]">
                          <span className="font-medium" style={{ color: AGENT[h.from]?.color }}>{AGENT[h.from]?.label ?? h.from}</span>
                          <ArrowRight size={12} className="text-ink-3" />
                          <span className="font-medium" style={{ color: AGENT[h.to]?.color }}>{AGENT[h.to]?.label ?? h.to}</span>
                          {h.n > 1 && <span className="text-[12px] text-ink-3 num">×{h.n}</span>}
                        </div>
                        {h.same ? <p className="text-[12.5px] text-ink-3">Same summary as <span className="mono">{h.same}</span>.</p> : (
                          <>
                            <p className="text-[13px] leading-relaxed text-ink-2">{clean(h.summary)}</p>
                            {h.open_questions?.length > 0 && <div className="mt-1 text-[12px] text-ink-3">Open: {h.open_questions.join(" · ")}</div>}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

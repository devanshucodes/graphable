"use client";
import { useEffect, useRef, useState } from "react";
import { arrow, post } from "@/lib/api";
import { Button, PageBar } from "@/components/ui";
import { ArrowUp, ArrowUpRight, Loader2, RotateCcw } from "lucide-react";

const GROUPS = [
  { title: "People and ownership", qs: ["Who knows vault best and what is the bus factor?", "Who owns promotions?", "Who should I ask about the redis cache?"] },
  { title: "Time travel", qs: ["Who owned promotions on 2026-06-15?", "Who owned the ledger on 2026-03-01?"] },
  { title: "Impact and history", qs: ["What breaks if redis-cache goes down?", "Who has fixed INC-0287 before?"] },
  { title: "Knowledge health", qs: ["Are the search runbooks stale?", "Are the vault runbooks stale?"] },
  { title: "Open questions, answered by Gemini from the graph", qs: ["How do we safely restart the service mesh?", "Why did we decide ADR-007?"] },
];
const INTENT: Record<string, string> = {
  ownership: "Ownership history", experts: "Expertise, computed from evidence", decision: "Decision trace", blast_radius: "Blast radius",
  staleness: "Staleness rules", who_fixed: "Incident memory", search: "Hybrid search + Gemini", error: "Error",
};
const pretty = (s?: string) => arrow((s ?? "").replace(/\b(svc|ds)_([a-z0-9-]+)/g, "$2"));

export default function BrainPage() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => { if (items.length) end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [items.length, busy]);

  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    setBusy(true); setQ("");
    setItems((xs) => [...xs, { question, pending: true }]);
    try {
      const r = await post("/brain/ask", { question });
      setItems((xs) => xs.map((x, i) => (i === xs.length - 1 ? r : x)));
    } catch (e: any) {
      setItems((xs) => xs.map((x, i) => (i === xs.length - 1 ? { question, answer: `Error: ${e.message}`, citations: [], tool_calls: [], intent: "error" } : x)));
    } finally { setBusy(false); input.current?.focus(); }
  };

  const box = (big: boolean) => (
    <form onSubmit={(e) => { e.preventDefault(); ask(q); }}
      className={`flex items-center gap-2 rounded-xl border border-line-2 bg-panel pl-4 pr-2 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_4px_16px_rgba(0,0,0,0.03)] focus-within:border-[#c4bbf0] focus-within:shadow-[0_0_0_3px_#f0edfc] ${big ? "h-12" : "h-11"}`}>
      <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about a service, a person, a decision or an incident…"
        className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-ink-3" />
      <button disabled={busy || !q.trim()} className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-white disabled:bg-[#e2e2de] disabled:text-ink-3">
        {busy ? <Loader2 size={15} className="animate-spin" /> : <ArrowUp size={16} />}
      </button>
    </form>
  );

  return (
    <>
      <PageBar crumbs={["Ask the Brain"]} actions={items.length > 0 && <Button variant="ghost" icon={<RotateCcw size={13} />} onClick={() => setItems([])} disabled={busy}>New conversation</Button>} />
      {items.length === 0 ? (
        <div className="mx-auto max-w-[720px] px-6 pb-16 pt-16">
          <h1 className="text-[24px] font-semibold tracking-[-0.02em]">What do you want to know?</h1>
          <p className="mt-1.5 text-[13.5px] text-ink-2">Answers come from Acme&apos;s company graph and cite the evidence they used. Questions with a date are answered as of that date.</p>
          <div className="mt-6">{box(true)}</div>
          <div className="mt-10 grid gap-x-10 gap-y-7 sm:grid-cols-2">
            {GROUPS.map((g) => (
              <div key={g.title}>
                <div className="mb-1.5 text-[12px] text-ink-3">{g.title}</div>
                {g.qs.map((s) => (
                  <button key={s} onClick={() => ask(s)} className="group flex w-full items-start gap-1.5 py-1 text-left text-[13px] text-ink-2 hover:text-ink">
                    <span className="flex-1">{s}</span><ArrowUpRight size={13} className="mt-0.5 shrink-0 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100" />
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="mx-auto flex min-h-[calc(100%-48px)] max-w-[760px] flex-col px-6">
          <div className="flex-1 divide-y divide-line pt-4">
            {items.map((it, i) => <Exchange key={i} it={it} />)}
            <div ref={end} />
          </div>
          <div className="sticky bottom-0 bg-gradient-to-t from-panel via-panel to-transparent pb-5 pt-6">{box(false)}</div>
        </div>
      )}
    </>
  );
}

function Exchange({ it }: { it: any }) {
  return (
    <article className="fade-in py-7">
      <h2 className="text-[17px] font-semibold tracking-[-0.01em]">{it.question}</h2>
      {it.pending ? (
        <div className="mt-3 flex items-center gap-2 text-[13px] text-ink-3"><Loader2 size={14} className="animate-spin" />Querying the graph…</div>
      ) : (
        <>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[12px] text-ink-3">
            <span>{INTENT[it.intent] ?? it.intent}</span><span>·</span><span>{(it.tool_calls ?? []).length} MCP tool calls</span>
            {it.citations?.length > 0 && <><span>·</span><span>{it.citations.length} {it.citations.length === 1 ? "source" : "sources"}</span></>}
          </div>
          <p className="mt-3 text-[14px] leading-7 text-ink">
            {pretty(it.answer)}
            {it.citations?.map((_: any, i: number) => <sup key={i} className="ml-0.5 rounded bg-hover px-1 text-[10px] font-medium text-ink-2">{i + 1}</sup>)}
          </p>
          {it.citations?.length > 0 && (
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {it.citations.map((c: any, i: number) => (
                <div key={c.id} className="rounded-lg border border-line px-3 py-2.5">
                  <div className="flex items-center gap-2 text-[11.5px] text-ink-3">
                    <span className="flex h-4 w-4 items-center justify-center rounded bg-hover text-[10px] font-medium text-ink-2">{i + 1}</span>
                    <span className="mono">{c.tool}</span><span className="mono ml-auto">{c.id}</span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-ink-2">{pretty(c.summary)}</div>
                  <div className="mt-1 text-[11.5px] text-ink-3">{c.nodes?.length ?? 0} graph nodes{c.edges?.length ? ` · ${c.edges.length} edges` : ""}</div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </article>
  );
}

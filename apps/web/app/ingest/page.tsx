"use client";
import { useEffect, useState } from "react";
import { fmtDate, get, post } from "@/lib/api";
import { Avatar, Button, Empty, PageBar, Person, Prop, Props, Section } from "@/components/ui";
import { Hash, Loader2, SendHorizontal } from "lucide-react";

const EXAMPLES = [
  "FYI from today checkout is owned by Orbit, ping @lena for anything urgent",
  "Heads up: from today search is owned by Platform/SRE while Discovery reorganizes",
];
const DAY = 86400000;

export default function IngestPage() {
  const [people, setPeople] = useState<any[]>([]);
  const [author, setAuthor] = useState("");
  const [text, setText] = useState(EXAMPLES[0]);
  const [out, setOut] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { get("/people").then((p) => { setPeople(p); setAuthor(p.find((x: any) => x.handle === "lena")?.id ?? p[0]?.id); }); }, []);
  const me = people.find((p) => p.id === author);
  const send = async () => { setBusy(true); try { setOut(await post("/ingest/message", { author_id: author, text })); } finally { setBusy(false); } };
  const short = (id: string) => id.replace(/^(svc_|ds_|team_)/, "");

  // build a graph diff: new facts (+) and facts that were closed, not deleted (~)
  const diff: { op: "+" | "~"; text: string; note: string }[] = [];
  if (out) {
    for (const f of out.facts) {
      const own = out.ownership_now?.[f.dst];
      if (f.rel === "OWNS" && own) {
        own.history.filter((h: any) => h.valid_to && f.valid_from && Math.abs(h.valid_to - f.valid_from) < DAY)
          .forEach((h: any) => diff.push({ op: "~", text: `(${h.team})-[:OWNS]->(${short(f.dst)})`, note: `closed · valid_to ${fmtDate(h.valid_to)} · kept as history` }));
        diff.push({ op: "+", text: `(${own.owner?.team ?? short(f.src)})-[:OWNS]->(${short(f.dst)})`, note: `valid_from ${fmtDate(f.valid_from)} · source: this message` });
      } else {
        diff.push({ op: "+", text: `(${f.src.startsWith("thr_live") ? "message" : short(f.src)})-[:${f.rel}]->(${short(f.dst)})`, note: f.valid_from ? `valid_from ${fmtDate(f.valid_from)}` : "" });
      }
    }
  }

  return (
    <>
      <PageBar crumbs={["Live ingest"]} />
      <div className="max-w-[1180px] px-8 pb-12 pt-6">
        <h1 className="text-[22px] font-semibold tracking-[-0.015em]">Live ingest</h1>
        <p className="mt-1 max-w-[680px] text-[13.5px] text-ink-2">Post a chat message. The Ingestor extracts facts against a fixed schema, resolves names to graph entities and applies them as time-stamped updates.</p>

        <div className="mt-6 grid gap-10 lg:grid-cols-2">
          <Section title="Message">
            <div className="box overflow-hidden">
              <div className="flex h-10 items-center gap-1.5 border-b border-line px-3 text-[13px]"><Hash size={14} className="text-ink-3" /><span className="font-medium">announcements</span><span className="text-ink-3">· simulated Slack channel</span></div>
              <div className="px-3 pt-3">
                <div className="flex items-center gap-2">
                  <Avatar name={me?.name} size={24} />
                  <select value={author} onChange={(e) => setAuthor(e.target.value)} className="min-w-0 flex-1 bg-transparent text-[13px] font-medium outline-none">
                    {people.map((p) => <option key={p.id} value={p.id}>{p.name} (@{p.handle}) · {p.team}</option>)}
                  </select>
                </div>
                <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} className="mt-2 w-full resize-none bg-transparent pl-8 text-[14px] leading-relaxed outline-none" />
              </div>
              <div className="flex items-center justify-between gap-3 border-t border-line bg-sunken px-3 py-2">
                <div className="flex min-w-0 flex-wrap gap-x-3 text-[12px] text-ink-3">
                  <span>Try:</span>{EXAMPLES.map((x, i) => <button key={x} onClick={() => setText(x)} className="underline decoration-line-2 underline-offset-2 hover:text-ink">example {i + 1}</button>)}
                </div>
                <Button variant="primary" disabled={busy || !text.trim()} onClick={send} icon={busy ? <Loader2 size={13} className="animate-spin" /> : <SendHorizontal size={13} />}>Send</Button>
              </div>
            </div>
          </Section>

          <Section title="Graph diff" right={out ? `${diff.length} changes` : undefined}>
            {!out ? <Empty title="Nothing sent yet" sub="Ownership statements become OWNS relationships with a valid-from date. The previous owner is closed, not deleted." /> : (
              <div className="fade-in space-y-8">
                <div className="box mono overflow-hidden text-[12.5px]">
                  {diff.map((d, i) => (
                    <div key={i} className={`flex gap-3 px-3 py-2 ${i ? "border-t border-line" : ""} ${d.op === "+" ? "bg-[#f3fbf6]" : "bg-[#fffaf0]"}`}>
                      <span className={`w-3 shrink-0 font-semibold ${d.op === "+" ? "text-[#18794e]" : "text-[#a35200]"}`}>{d.op}</span>
                      <div className="min-w-0"><div className="text-ink">{d.text}</div>{d.note && <div className="mt-0.5 font-sans text-[12px] text-ink-3">{d.note}</div>}</div>
                    </div>
                  ))}
                </div>
                {Object.entries(out.ownership_now ?? {}).map(([id, o]: any) => (
                  <Section key={id} title={<>Ownership of <span className="mono">{short(id)}</span></>}>
                    <Props>
                      <Prop k="Owner now">{o.owner?.team}</Prop>
                      <Prop k="On call"><div className="flex flex-wrap gap-3">{o.oncall?.map((x: any) => <Person key={x.id} name={x.name} />)}</div></Prop>
                    </Props>
                    <ol className="mt-3">
                      {[...o.history].reverse().map((h: any, i: number, arr: any[]) => (
                        <li key={i} className="relative pb-3 pl-5">
                          {i < arr.length - 1 && <span className="absolute left-[3.5px] top-3 h-full w-px bg-line-2" />}
                          <span className={`absolute left-0 top-[5px] h-2 w-2 rounded-full ${h.valid_to ? "bg-[#c9c9c4]" : "bg-green"}`} />
                          <div className="text-[13px] font-medium">{h.team}{!h.valid_to && <span className="ml-1.5 text-[12px] font-normal text-[#18794e]">current</span>}</div>
                          <div className="text-[12px] text-ink-3">{fmtDate(h.valid_from)} → {h.valid_to ? fmtDate(h.valid_to) : "now"} · <span className="mono">{h.source}</span></div>
                        </li>
                      ))}
                    </ol>
                  </Section>
                ))}
              </div>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

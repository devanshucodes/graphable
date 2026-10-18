import { fmtDate } from "@/lib/api";

export type Ownership = { team: string; valid_from?: number | null; valid_to?: number | null; source?: string | null };

/** OWNS facts over time, newest first. Closed facts stay listed: they are history, not deletions.
 *  `showSource` prints the raw source id (catalog or the message id); otherwise chat-sourced rows get a hint. */
export default function OwnershipHistory({ history, showSource = false, className = "" }: { history?: Ownership[] | null; showSource?: boolean; className?: string }) {
  const rows = [...(history ?? [])].reverse();
  return (
    <ol className={className}>
      {rows.map((h, i) => (
        <li key={i} className="relative pb-3 pl-5">
          {i < rows.length - 1 && <span className="absolute left-[3.5px] top-3 h-full w-px bg-line-2" />}
          <span className={`absolute left-0 top-[5px] h-2 w-2 rounded-full ${h.valid_to ? "bg-[#c9c9c4]" : "bg-green"}`} />
          <div className="text-[13px] font-medium">{h.team}{!h.valid_to && <span className="ml-1.5 text-[12px] font-normal text-[#18794e]">current</span>}</div>
          <div className="text-[12px] text-ink-3">
            {fmtDate(h.valid_from)} → {h.valid_to ? fmtDate(h.valid_to) : "now"}
            {showSource ? h.source && <> · <span className="mono">{h.source}</span></> : h.source && h.source !== "catalog" && <span className="text-brand"> · from chat</span>}
          </div>
        </li>
      ))}
    </ol>
  );
}

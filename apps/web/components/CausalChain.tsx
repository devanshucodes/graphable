"use client";
/** Deterministic causal diagram: boxes on a grid, labelled arrows, labels outside shapes.
 *  Built from the crew's verified conclusion (path, change, owner, experts, memory). */

export type CKind = "incident" | "alerting" | "service" | "root" | "change" | "person" | "team" | "past" | "runbook";
export type CNode = { id: string; label: string; sub?: string; kind: CKind; col: number; row: number };
export type CEdge = { from: string; to: string; label: string; hot?: boolean; dashed?: boolean };

const KIND: Record<CKind, { tag: string; dot: string; bg?: string; border?: string; tagColor?: string }> = {
  incident: { tag: "Incident", dot: "#f76b15", bg: "#fffaf5", border: "#f6dcc3" },
  alerting: { tag: "Alerting service", dot: "#f5a623" },
  service: { tag: "Service", dot: "#5b5bd6" },
  root: { tag: "Root cause", dot: "#e5484d", bg: "#fff6f6", border: "#f3b9bb", tagColor: "#ce2c31" },
  change: { tag: "Change", dot: "#d6409f" },
  person: { tag: "Person", dot: "#30a46c" },
  team: { tag: "Owning team", dot: "#8e4ec6" },
  past: { tag: "Past incident", dot: "#f76b15" },
  runbook: { tag: "Runbook", dot: "#e2a336" },
};
const W = 164, H = 58, CW = 204, RH = 108, PAD = 12, TOP = 26;
const pill = (label: string) => label.length * 6 + 14; // edge-label pill width at 10.5px

export default function CausalChain({ nodes, edges, rowLabels }: { nodes: CNode[]; edges: CEdge[]; rowLabels?: string[] }) {
  const pos = new Map(nodes.map((n) => [n.id, { x: PAD + n.col * CW, y: TOP + n.row * RH }]));
  const cols = Math.max(...nodes.map((n) => n.col)) + 1, rows = Math.max(...nodes.map((n) => n.row)) + 1;
  const vw = PAD * 2 + cols * CW - (CW - W), vh = TOP + PAD + rows * RH - (RH - H);
  const anchor = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const ac = { x: a.x + W / 2, y: a.y + H / 2 }, bc = { x: b.x + W / 2, y: b.y + H / 2 };
    if (Math.abs(ac.y - bc.y) < 1) return ac.x < bc.x ? [{ x: a.x + W, y: ac.y }, { x: b.x, y: bc.y }] : [{ x: a.x, y: ac.y }, { x: b.x + W, y: bc.y }];
    return ac.y < bc.y ? [{ x: ac.x, y: a.y + H }, { x: bc.x, y: b.y }] : [{ x: ac.x, y: a.y }, { x: bc.x, y: b.y + H }];
  };
  // 1:1 when it fits; a narrower parent scales it down to fit (never below 55%, past that the parent scrolls sideways)
  return (
    <svg width={vw} height={vh} viewBox={`0 0 ${vw} ${vh}`} className="mx-auto block h-auto" style={{ width: "100%", maxWidth: vw, minWidth: Math.round(vw * 0.55) }}>
      <defs>
        {[["n", "#b9b9b4"], ["h", "#e5484d"], ["d", "#8f7fe0"]].map(([k, c]) => (
          <marker key={k} id={`cc-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={c} />
          </marker>
        ))}
        <filter id="cc-shadow" x="-10%" y="-10%" width="120%" height="140%"><feDropShadow dx="0" dy="1" stdDeviation="1.2" floodColor="#000" floodOpacity="0.05" /></filter>
      </defs>
      {rowLabels?.slice(0, rows).map((l, r) => (
        <text key={l} x={PAD + 2} y={TOP + r * RH - 9} fontSize="11" fill="#6f6f69" fontWeight="500">{l}</text>
      ))}
      {edges.map((e, i) => {
        const a = pos.get(e.from), b = pos.get(e.to);
        if (!a || !b) return null;
        const [p, q] = anchor(a, b);
        const color = e.hot ? "#e5484d" : e.dashed ? "#8f7fe0" : "#b9b9b4";
        return <line key={i} x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={color} strokeWidth={e.hot ? 2 : 1.3} strokeDasharray={e.dashed ? "4 4" : undefined}
          markerEnd={`url(#cc-${e.hot ? "h" : e.dashed ? "d" : "n"})`} />;
      })}
      {edges.map((e, i) => {
        const a = pos.get(e.from), b = pos.get(e.to);
        if (!a || !b) return null;
        const [p, q] = anchor(a, b);
        // the gap between two boxes in a row is narrower than a label, so a sideways edge is labelled just above its row
        const mx = (p.x + q.x) / 2, my = p.y === q.y ? Math.min(a.y, b.y) - 11 : (p.y + q.y) / 2, tw = pill(e.label);
        return (
          <g key={`l${i}`}>
            <rect x={mx - tw / 2} y={my - 9} width={tw} height={18} rx={9} fill="#fff" stroke={e.hot ? "#f6c4c6" : "#e2e2de"} />
            <text x={mx} y={my + 3.5} textAnchor="middle" fontSize="10.5" fontWeight="500" fill={e.hot ? "#ce2c31" : "#4f4f4b"} letterSpacing=".2">{e.label.toLowerCase()}</text>
          </g>
        );
      })}
      {nodes.map((n) => {
        const p = pos.get(n.id)!, k = KIND[n.kind];
        const label = n.label.length > 17 ? n.label.slice(0, 16) + "…" : n.label;
        const sub = n.sub && n.sub.length > 23 ? n.sub.slice(0, 22) + "…" : n.sub;
        return (
          <g key={n.id}>
            <rect x={p.x} y={p.y} width={W} height={H} rx={8} fill={k.bg ?? "#fff"} stroke={k.border ?? "#dcdcd8"} filter="url(#cc-shadow)" />
            <circle cx={p.x + 15} cy={p.y + 15.5} r={3.5} fill={k.dot} />
            <text x={p.x + 24} y={p.y + 19} fontSize="11" fontWeight="500" fill={k.tagColor ?? "#6f6f69"}>{k.tag}</text>
            <text x={p.x + 11} y={p.y + 37} fontSize="14" fontWeight="600" fill="#1c1c1a"><title>{n.label}</title>{label}</text>
            {sub && <text x={p.x + 11} y={p.y + 51} fontSize="12" fill="#4f4f4b"><title>{n.sub}</title>{sub}</text>}
          </g>
        );
      })}
    </svg>
  );
}

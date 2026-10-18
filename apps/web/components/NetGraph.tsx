"use client";
/** Layered graph view: nodes sit in named lanes (columns or rows), labels sit next to nodes,
 *  hovering a node traces its connections. Layout is deterministic (layer assignment +
 *  barycenter ordering), so the same data always draws the same picture. */
import { useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";

export type NNode = { id: string; label: string; type: string; layer: number; color: string; ring?: string; square?: boolean; strong?: boolean; detail?: string };
export type NEdge = { source: string; target: string; type: string; hot?: boolean; faint?: boolean };
type P = { x: number; y: number };
type Dir = "lr" | "tb";

/** Longest-path layers over the graph with DFS back edges ignored (cycle-safe). */
export function dagLayers(ids: string[], edges: { source: string; target: string }[]): Map<string, number> {
  const set = new Set(ids), out = new Map<string, string[]>(ids.map((i) => [i, []]));
  edges.forEach((e) => { if (set.has(e.source) && set.has(e.target) && e.source !== e.target) out.get(e.source)!.push(e.target); });
  const state = new Map<string, number>(), back = new Set<string>();
  const dfs = (u: string) => {
    state.set(u, 1);
    for (const v of [...out.get(u)!].sort()) { if (state.get(v) === 1) back.add(`${u}>${v}`); else if (!state.get(v)) dfs(v); }
    state.set(u, 2);
  };
  [...ids].sort().forEach((u) => { if (!state.get(u)) dfs(u); });
  const L = new Map(ids.map((i) => [i, 0]));
  for (let it = 0, ch = true; ch && it < ids.length; it++) {
    ch = false;
    for (const [u, vs] of out) for (const v of vs) if (!back.has(`${u}>${v}`) && L.get(u)! + 1 > L.get(v)!) { L.set(v, L.get(u)! + 1); ch = true; }
  }
  return L;
}

function layout(nodes: NNode[], edges: NEdge[], w: number, h: number, dir: Dir, gutter: number) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const es = edges.filter((e) => byId.has(e.source) && byId.has(e.target) && e.source !== e.target);
  const adj = new Map<string, Set<string>>(nodes.map((n) => [n.id, new Set()]));
  es.forEach((e) => { adj.get(e.source)!.add(e.target); adj.get(e.target)!.add(e.source); });
  const layers = [...new Set(nodes.map((n) => n.layer))].sort((a, b) => a - b);
  const li = new Map(layers.map((l, i) => [l, i]));
  const cols: string[][] = layers.map(() => []);
  nodes.forEach((n) => cols[li.get(n.layer)!].push(n.id));
  cols.forEach((c) => c.sort((a, b) => Number(!!byId.get(b)!.strong) - Number(!!byId.get(a)!.strong) || byId.get(a)!.label.localeCompare(byId.get(b)!.label)));

  const xy = new Map<string, P>(), chars = new Map<string, number>(), lanes: { layer: number; pos: number; n: number }[] = [];
  let H = h;
  const place = () => {
    if (dir === "lr") {
      const top = 46, bottom = 52, left = 28, right = 168, span = w - left - right, inner = H - top - bottom, n = cols.length;
      const sp = n > 1 ? span / (n - 1) : 0;
      cols.forEach((c, L) => {
        const x = n > 1 ? left + L * sp : w / 2;
        lanes[L] = { layer: layers[L], pos: x, n: c.length };
        const step = Math.min(34, inner / Math.max(c.length, 1));
        const ch = Math.max(8, Math.floor(((L === n - 1 ? right : sp) - 24) / 6.5));
        c.forEach((id, i) => { xy.set(id, { x, y: top + inner / 2 + (i - (c.length - 1) / 2) * step }); chars.set(id, ch); });
      });
    } else {
      const left = gutter, right = 20, inner = w - left - right, MINX = 116, ROW = 64, SUB = 32;
      let y = 34;
      cols.forEach((c, L) => {
        const sub = Math.max(1, Math.ceil(c.length / Math.max(1, Math.floor(inner / MINX))));
        lanes[L] = { layer: layers[L], pos: y + ((sub - 1) * SUB) / 2, n: c.length };
        const rows: string[][] = Array.from({ length: sub }, () => []);
        c.forEach((id, i) => rows[i % sub].push(id));
        rows.forEach((r, ri) => {
          const step = Math.min(176, inner / r.length);
          const ch = Math.max(6, Math.floor((step - 10) / 6.3));
          r.forEach((id, j) => {
            xy.set(id, { x: left + inner / 2 + (j - (r.length - 1) / 2) * step + (sub > 1 ? ((ri - (sub - 1) / 2) * step) / sub : 0), y: y + ri * SUB });
            chars.set(id, ch);
          });
        });
        y += (sub - 1) * SUB + ROW;
      });
      H = y - ROW + 72;
    }
  };
  place();
  // barycenter sweeps: order each lane by the mean position of its neighbours to reduce crossings
  const cross = (id: string) => (dir === "lr" ? xy.get(id)!.y : xy.get(id)!.x);
  for (let it = 0; it < 10; it++) {
    const order = it % 2 ? [...cols.keys()].reverse() : [...cols.keys()];
    for (const L of order) {
      const ref = it % 2 ? L + 1 : L - 1;
      const bc = new Map<string, number>();
      cols[L].forEach((id) => {
        const ns = [...adj.get(id)!];
        let pick = ns.filter((x) => li.get(byId.get(x)!.layer) === ref);
        if (!pick.length) pick = ns.filter((x) => li.get(byId.get(x)!.layer) !== L);
        bc.set(id, pick.length ? pick.reduce((s, x) => s + cross(x), 0) / pick.length : cross(id));
      });
      cols[L].sort((a, b) => bc.get(a)! - bc.get(b)!);
      place();
    }
  }
  return { xy, chars, lanes, adj, es, H };
}

const trunc = (s: string, n = 26) => (s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s);

export default function NetGraph({ nodes, edges, height = 520, dir = "lr", lanes: laneLabels, focus, onNodeClick, gutter = 104 }: {
  nodes: NNode[]; edges: NEdge[]; height?: number; dir?: Dir; lanes?: Record<number, string>; focus?: string | null; onNodeClick?: (id: string) => void; gutter?: number;
}) {
  const outer = useRef<HTMLDivElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  const [ow, setOw] = useState(0); // available width: below 640px a left-to-right graph keeps 960px and scrolls sideways
  const [t, setT] = useState({ k: 1, x: 0, y: 0 });
  const [hover, setHover] = useState<string | null>(null);
  const [pin, setPin] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);

  useEffect(() => {
    const el = wrap.current; if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth)); ro.observe(el); setW(el.clientWidth);
    const wheel = (ev: WheelEvent) => {
      if (!ev.ctrlKey && !ev.metaKey) return; // plain scroll keeps scrolling the page; pinch / cmd-scroll zooms
      ev.preventDefault();
      const r = el.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
      setT((o) => { const k = Math.min(3, Math.max(0.4, o.k * Math.exp(-ev.deltaY * 0.01))); return { k, x: px - ((px - o.x) * k) / o.k, y: py - ((py - o.y) * k) / o.k }; });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => { ro.disconnect(); el.removeEventListener("wheel", wheel); };
  }, []);
  useEffect(() => {
    const el = outer.current; if (!el) return;
    const ro = new ResizeObserver(() => setOw(el.clientWidth)); ro.observe(el); setOw(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { setT({ k: 1, x: 0, y: 0 }); setPin(null); }, [nodes, edges]);

  const lay = useMemo(() => (w ? layout(nodes, edges, w, height, dir, gutter) : null), [nodes, edges, w, height, dir, gutter]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const H = dir === "tb" ? (lay?.H ?? height) : height;
  const active = hover ?? pin ?? null; // `focus` only rings its node; dimming follows hover / pin
  const near = active && lay?.adj.has(active) ? new Set([active, ...lay.adj.get(active)!]) : null;
  const zoom = (f: number) => setT((o) => { const k = Math.min(3, Math.max(0.4, o.k * f)), cx = w / 2, cy = H / 2; return { k, x: cx - ((cx - o.x) * k) / o.k, y: cy - ((cy - o.y) * k) / o.k }; });

  const curve = (a: P, b: P) => {
    if (dir === "lr") {
      if (Math.abs(a.x - b.x) < 1) return `M${a.x},${a.y} C${a.x - 52},${a.y} ${b.x - 52},${b.y} ${b.x},${b.y}`;
      const m = (a.x + b.x) / 2; return `M${a.x},${a.y} C${m},${a.y} ${m},${b.y} ${b.x},${b.y}`;
    }
    if (Math.abs(a.y - b.y) < 1) return `M${a.x},${a.y} C${a.x},${a.y - 42} ${b.x},${b.y - 42} ${b.x},${b.y}`;
    const m = (a.y + b.y) / 2; return `M${a.x},${a.y} C${a.x},${m} ${b.x},${m} ${b.x},${b.y}`;
  };
  const isOn = (e: NEdge) => !!active && (e.source === active || e.target === active);
  const sorted = lay ? lay.es.filter((e) => !e.faint || isOn(e)).sort((a, b) => Number(isOn(a)) * 2 + Number(!!a.hot) - (Number(isOn(b)) * 2 + Number(!!b.hot))) : [];
  const onEdges = sorted.filter(isOn), onTypes = new Set(onEdges.map((e) => e.type));
  const seenLabel = new Set<string>();
  const labelled = onEdges.filter((e) => { const k = `${e.type}|${byId.get(e.source === active ? e.target : e.source)?.layer}`; return !seenLabel.has(k) && !!seenLabel.add(k); });
  const showLabels = !!active && labelled.length <= 6 && onTypes.size > 1; // one shared type adds nothing; it is shown in the info bar
  const an = active ? byId.get(active) : null, nConn = an ? lay?.adj.get(an.id)?.size ?? 0 : 0;
  const lanes = new Set(nodes.map((n) => n.layer)).size;
  const narrow = dir === "lr" && ow > 0 && ow < Math.max(480, lanes * 160); // lane titles need ~150px each, so a narrow box scrolls instead of squeezing
  const hint = "Hover a node to trace its connections · drag to pan · pinch or ⌘-scroll to zoom";
  const info = an ? `${an.type} · ${an.detail ?? an.label} · ${nConn} ${nConn === 1 ? "connection" : "connections"}${onTypes.size === 1 ? ` (${[...onTypes][0].replaceAll("_", " ").toLowerCase()})` : ""}` : hint;

  return (
    <div ref={outer} className="w-full">
      {/* toolbar above the canvas, so hover details and zoom are never below the fold */}
      <div className="mb-2 flex min-h-7 items-center gap-3">
        <div className="min-w-0 flex-1 truncate text-[12px] text-ink-2" title={info}>
          {an ? <><i className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: an.color }} /><b className="font-medium text-ink">{an.type}</b> · {an.detail ?? an.label} · {nConn} {nConn === 1 ? "connection" : "connections"}{onTypes.size === 1 ? ` (${[...onTypes][0].replaceAll("_", " ").toLowerCase()})` : ""}</>
            : <span className="text-ink-3">{hint}</span>}
        </div>
        <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-line bg-panel p-0.5 shadow-[0_1px_2px_rgba(0,0,0,0.05)]">
          <button title="Zoom in" aria-label="Zoom in" onClick={() => zoom(1.25)} className="rounded p-1 text-ink-3 hover:bg-hover hover:text-ink"><Plus size={14} /></button>
          <button title="Zoom out" aria-label="Zoom out" onClick={() => zoom(0.8)} className="rounded p-1 text-ink-3 hover:bg-hover hover:text-ink"><Minus size={14} /></button>
          <button title="Reset view" aria-label="Reset view" onClick={() => setT({ k: 1, x: 0, y: 0 })} className="rounded p-1 text-ink-3 hover:bg-hover hover:text-ink"><Maximize2 size={14} /></button>
        </div>
      </div>
      <div className={narrow ? "scroll overflow-x-auto rounded-lg border border-line" : ""}>
        <div ref={wrap} className={`relative w-full select-none overflow-hidden bg-panel ${narrow ? "" : "rounded-lg border border-line"}`} style={{ height: H, minWidth: narrow ? 960 : undefined }}>
          {lay && (
            <svg width={w} height={H} className="block"
              onPointerDown={(e) => { if (narrow && e.pointerType === "touch") return; /* let the finger scroll the strip */ if ((e.target as Element).tagName === "svg" || (e.target as Element).getAttribute("data-bg")) { drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, moved: false }; setDragging(true); (e.currentTarget as Element).setPointerCapture(e.pointerId); } }}
              onPointerMove={(e) => { const d = drag.current; if (!d) return; const dx = e.clientX - d.x, dy = e.clientY - d.y; if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true; setT((o) => ({ ...o, x: d.tx + dx, y: d.ty + dy })); }}
              onPointerUp={() => { if (drag.current && !drag.current.moved) setPin(null); drag.current = null; setDragging(false); }}
              onPointerCancel={() => { drag.current = null; setDragging(false); }}
              style={{ cursor: dragging ? "grabbing" : "grab", touchAction: narrow ? "pan-x pan-y" : "none" }}>
              <defs>
                {[["on", "#6e56cf"], ["hot", "#e5484d"]].map(([id, c]) => (
                  <marker key={id} id={`ng-${id}`} viewBox="0 0 10 10" refX={19} refY={5} markerWidth={7} markerHeight={7} markerUnits="userSpaceOnUse" orient="auto">
                    <path d="M0,0 L10,5 L0,10 z" fill={c} />
                  </marker>
                ))}
              </defs>
              <rect data-bg="1" width={w} height={H} fill="transparent" />
              <g transform={`translate(${t.x},${t.y}) scale(${t.k})`}>
                {laneLabels && lay.lanes.map((l, i) => laneLabels[l.layer] && (dir === "lr" ? (
                  <text key={l.layer} x={l.pos - 7} y={22} fontSize={11.5} fontWeight={500} fill="#4f4f4b">{laneLabels[l.layer]}<tspan fill="#6f6f69"> {l.n}</tspan></text>
                ) : (
                  <g key={l.layer}>
                    {i > 0 && <line x1={0} x2={w} y1={l.pos - 32} y2={l.pos - 32} stroke="#f0f0ee" />}
                    <text x={16} y={l.pos + 4} fontSize={11.5} fontWeight={500} fill="#4f4f4b">{laneLabels[l.layer]}</text>
                  </g>
                )))}
                {sorted.map((e, i) => {
                  const a = lay.xy.get(e.source)!, b = lay.xy.get(e.target)!, on = isOn(e);
                  const stroke = e.hot ? "#e5484d" : on ? "#6e56cf" : "#c9c9c4";
                  const op = active ? (on ? 1 : e.hot ? 0.3 : 0.07) : e.hot ? 1 : e.faint ? 0.45 : 0.65;
                  return <path key={i} d={curve(a, b)} fill="none" stroke={stroke} strokeOpacity={op} strokeWidth={e.hot ? 2.2 : on ? 1.6 : 1}
                    strokeDasharray={e.faint && !on ? "3 3" : undefined} markerEnd={on || (e.hot && !active) ? `url(#ng-${e.hot ? "hot" : "on"})` : undefined} />;
                })}
                {showLabels && labelled.map((e, i) => {
                  const a = lay.xy.get(e.source)!, b = lay.xy.get(e.target)!;
                  return <text key={`l${i}`} x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 3} textAnchor="middle" fontSize={9.5} fontWeight={600} fill={e.hot ? "#ce2c31" : "#5746af"}
                    stroke="#fff" strokeWidth={3} paintOrder="stroke" strokeLinejoin="round" letterSpacing=".2">{e.type.replaceAll("_", " ")}</text>;
                })}
                {nodes.map((n) => {
                  const p = lay.xy.get(n.id); if (!p) return null;
                  const r = n.strong ? 7.5 : 6, dim = near && !near.has(n.id), isA = n.id === active, ring = isA || n.id === focus;
                  const lx = dir === "lr" ? p.x + r + 6 : p.x, ly = dir === "lr" ? p.y + 4 : p.y + r + 14;
                  return (
                    <g key={n.id} opacity={dim ? 0.2 : 1} style={{ cursor: "pointer" }}
                      onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)}
                      onClick={(ev) => { ev.stopPropagation(); setPin((x) => (x === n.id ? null : n.id)); onNodeClick?.(n.id); }}>
                      <circle cx={p.x} cy={p.y} r={r + 7} fill="transparent" />
                      {(n.ring || ring) && <circle cx={p.x} cy={p.y} r={r + 4} fill="none" stroke={ring ? "#6e56cf" : n.ring} strokeWidth={2} />}
                      {n.square
                        ? <rect x={p.x - r} y={p.y - r} width={r * 2} height={r * 2} rx={2.5} fill={n.color} stroke="#fff" strokeWidth={1.5} />
                        : <circle cx={p.x} cy={p.y} r={r} fill={n.color} stroke="#fff" strokeWidth={1.5} />}
                      <text x={lx} y={ly} textAnchor={dir === "lr" ? "start" : "middle"} fontSize={12} fontWeight={n.strong || isA ? 600 : 400}
                        fill={n.strong || isA ? "#1c1c1a" : "#4b4b47"} stroke="#fff" strokeWidth={4} paintOrder="stroke" strokeLinejoin="round">{trunc(n.label, n.strong ? 34 : lay.chars.get(n.id))}</text>
                    </g>
                  );
                })}
              </g>
            </svg>
          )}
        </div>
      </div>
    </div>
  );
}

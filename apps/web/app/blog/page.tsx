import type { Metadata } from "next";
import { BLOG_HTML } from "./content";

export const metadata: Metadata = {
  title: "Graphable blog: an AI on-call crew with a FalkorDB brain",
  description: "How we built Graphable for Graph Hacks: AI agents that walk a FalkorDB graph of the company. Switch the graph off and accuracy drops from 84% to 6%.",
};

const btn = "inline-flex h-9 items-center rounded-lg px-3.5 text-[13.5px] font-medium";

export default function Blog() {
  return (
    <>
      <header className="sticky top-0 z-20 border-b border-[#e3e1da] bg-[#fbfaf7]/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1100px] items-center justify-between gap-3 px-5">
          <a href="/" className="flex min-w-0 items-center gap-2.5">
            <img src="/icon.svg" alt="" width={22} height={22} />
            <span className="text-[15px] font-semibold tracking-[-0.01em]">Graphable</span>
            <span className="hidden text-[14px] text-[#8a8981] sm:inline">· Blog</span>
          </a>
          <nav className="flex items-center gap-2">
            <a href="https://github.com/devanshucodes/graphable" target="_blank" rel="noopener" className={`${btn} border border-[#e3e1da] bg-white text-[#16171a] hover:border-[#c9c7bf]`}>GitHub</a>
            <a href="/" className={`${btn} bg-[#16171a] text-white hover:bg-[#2b2c31]`}>Open the live demo →</a>
          </nav>
        </div>
      </header>
      <div className="mx-auto max-w-[820px] px-5 pt-12 sm:px-8">
        <p className="font-mono text-[12.5px] uppercase tracking-[0.12em] text-[#6e56cf]">Graph Hacks · Team Graphable</p>
      </div>
      <article className="blog mx-auto max-w-[820px] px-5 pb-16 pt-4 sm:px-8" dangerouslySetInnerHTML={{ __html: BLOG_HTML }} />
      <footer className="border-t border-[#e3e1da]">
        <div className="mx-auto flex max-w-[820px] flex-wrap items-center justify-between gap-3 px-5 py-8 text-[14px] text-[#5a5b62] sm:px-8">
          <span>Team Graphable · Devanshu Saxena · Prins Kumar · Vaibhav Tyagi</span>
          <span className="flex gap-4"><a href="/" className="underline underline-offset-4">Live demo</a><a href="https://github.com/devanshucodes/graphable" target="_blank" rel="noopener" className="underline underline-offset-4">GitHub</a></span>
        </div>
      </footer>
    </>
  );
}

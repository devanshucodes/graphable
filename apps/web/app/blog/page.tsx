import type { Metadata } from "next";
import { PageBar } from "@/components/ui";
import { BLOG_HTML } from "./content";

export const metadata: Metadata = {
  title: "Graphable blog: an AI on-call crew with a FalkorDB brain",
  description: "How we built Graphable for Graph Hacks: AI agents that walk a FalkorDB graph of the company. Switch the graph off and accuracy drops from 84% to 6%.",
};

export default function Blog() {
  return (
    <>
      <PageBar crumbs={["Blog"]} />
      <article className="blog mx-auto max-w-[820px] px-5 pb-20 pt-10 sm:px-8" dangerouslySetInnerHTML={{ __html: BLOG_HTML }} />
    </>
  );
}

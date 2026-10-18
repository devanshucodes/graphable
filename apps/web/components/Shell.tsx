"use client";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";

/** App chrome (sidebar + panel) for the product pages; the blog is a standalone article page. */
export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() ?? "";
  if (path.startsWith("/blog")) return <div className="h-dvh overflow-y-auto bg-[#fbfaf7]">{children}</div>;
  return (
    <div className="flex h-dvh flex-col overflow-hidden md:flex-row">
      <Sidebar />
      <main className="min-h-0 min-w-0 flex-1 md:py-2 md:pr-2">
        <div id="panel" className="scroll h-full overflow-y-auto bg-panel md:rounded-[10px] md:border md:border-line md:shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
          {children}
        </div>
      </main>
    </div>
  );
}

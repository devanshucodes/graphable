import "./globals.css";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Sidebar from "@/components/Sidebar";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Graphable",
  description: "An AI on-call crew with a graph brain, built on FalkorDB",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geist.variable} ${mono.variable}`}>
      <body>
        <div className="flex h-screen overflow-hidden">
          <Sidebar />
          <main className="min-w-0 flex-1 md:py-2 md:pr-2">
            <div id="panel" className="scroll h-full overflow-y-auto bg-panel md:rounded-[10px] md:border md:border-line md:shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}

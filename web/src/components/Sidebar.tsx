"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useApi } from "@/lib/api";
import type { AppConfig } from "@/lib/types";

const LINKS = [
  { href: "/", label: "New job", icon: "M12 5v14M5 12h14" },
  { href: "/jobs", label: "Jobs", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/healing", label: "Layout changes", icon: "M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4" },
  { href: "/evaluation", label: "Evaluation", icon: "M5 19V9M12 19V5M19 19v-7" },
  { href: "/audit", label: "Audit log", icon: "M7 4h10l3 3v13H4V4zM8 11h8M8 15h6" },
];

export function Sidebar() {
  const path = usePathname();
  const { data: config } = useApi<AppConfig>("/api/config");
  return (
    <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col bg-night text-white">
      <Link href="/" className="flex items-center gap-2.5 px-5 pt-6 pb-7">
        <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
          <path d="M4 7h24l-9 11v7l-6 3V18z" fill="none" stroke="#a9a3ff" strokeWidth="2.4" strokeLinejoin="round" />
          <circle cx="10" cy="11" r="1.4" fill="#5eead4" />
          <circle cx="16" cy="11" r="1.4" fill="#5eead4" />
          <circle cx="22" cy="11" r="1.4" fill="#5eead4" />
        </svg>
        <div>
          <div className="text-xl font-semibold tracking-tight">Sift</div>
          <div className="text-[11px] text-indigo-200/70">web data, validated</div>
        </div>
      </Link>
      <nav className="flex flex-col gap-0.5 px-3 text-sm">
        {LINKS.map((link) => {
          const active = link.href === "/" ? path === "/" : path.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`flex items-center gap-3 rounded-md px-3 py-2 transition ${active ? "bg-night-soft text-white" : "text-indigo-100/70 hover:bg-night-soft/60 hover:text-white"}`}
            >
              <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d={link.icon} />
              </svg>
              {link.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto space-y-2 border-t border-white/10 px-5 py-4 text-[11px] leading-relaxed text-indigo-100/60">
        {config ? (
          <>
            <div>
              <span className="text-indigo-100/40">model</span>
              <div className="mono break-all text-indigo-50/90">{config.model}</div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {config.require_free_models ? <span className="rounded bg-teal/30 px-1.5 py-0.5 text-teal-100">free-only guard</span> : null}
              {config.respect_robots ? <span className="rounded bg-white/10 px-1.5 py-0.5">robots.txt</span> : null}
              <span className="rounded bg-white/10 px-1.5 py-0.5">{config.min_seconds_per_domain}s / host</span>
              {config.pii_guard ? <span className="rounded bg-white/10 px-1.5 py-0.5">PII guard</span> : null}
            </div>
          </>
        ) : (
          <div>API not reachable</div>
        )}
      </div>
    </aside>
  );
}

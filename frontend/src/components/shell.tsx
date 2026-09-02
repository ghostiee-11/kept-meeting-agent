"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { TooltipProvider } from "@/components/ui/tooltip";

const NAV = [
  { href: "/", label: "Run", hint: "Process a transcript" },
  { href: "/execution", label: "Execution", hint: "Who owes what" },
  { href: "/people", label: "People", hint: "One person's promises" },
  { href: "/ops", label: "Ops", hint: "Traces, cost, tasks" },
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex min-h-dvh">
        <nav className="border-rule bg-surface sticky top-0 flex h-dvh w-48 shrink-0 flex-col border-r">
          <Link
            href="/"
            className="border-rule block border-b px-5 py-5 no-underline"
          >
            <span className="t-display block text-[1.5rem] leading-none">
              Kept
            </span>
            <span className="text-paper-muted mt-1.5 block text-[0.6875rem] leading-tight">
              Meetings make promises.
              <br />
              Kept makes them accountable.
            </span>
          </Link>

          <ul className="flex flex-col py-2">
            {NAV.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/"
                  : pathname.startsWith(item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={`hover:bg-surface-raised block border-l-2 px-5 py-2 transition-colors ${
                      active
                        ? "border-l-kept text-paper"
                        : "text-paper-dim border-l-transparent"
                    }`}
                  >
                    <span className="block text-[0.8125rem] font-medium">
                      {item.label}
                    </span>
                    <span className="text-paper-muted block text-[0.6875rem]">
                      {item.hint}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </TooltipProvider>
  );
}

/**
 * Light mode is greenbar paper rather than a washed-out dark theme, so it is
 * worth being able to reach deliberately instead of only through the OS.
 */
function ThemeToggle() {
  return (
    <button
      onClick={() => {
        const root = document.documentElement;
        const current =
          root.dataset.theme ??
          (matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark"
            : "light");
        root.dataset.theme = current === "dark" ? "light" : "dark";
      }}
      className="t-eyebrow hover:text-paper transition-colors"
    >
      Invert
    </button>
  );
}

export function PageHeader({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="border-rule flex items-baseline justify-between gap-6 border-b px-8 py-5">
      <h1 className="t-heading text-[1.25rem]">{title}</h1>
      <div className="flex items-center gap-5">
        {children}
        <ThemeToggle />
      </div>
    </header>
  );
}

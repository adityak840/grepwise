"use client";

import { Moon, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";

import { SettingsDialog } from "@/components/settings-dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Ask" },
  { href: "/sources", label: "Sources" },
  { href: "/evals", label: "Evals" },
  { href: "/about", label: "About" },
];

export function SiteHeader() {
  const pathname = usePathname();
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <header className="bg-background/80 sticky top-0 z-40 border-b backdrop-blur">
      <div className="mx-auto flex h-14 max-w-4xl items-center gap-0.5 px-3 sm:gap-1 sm:px-4">
        <Link href="/" className="mr-1 font-mono text-sm font-semibold tracking-tight sm:mr-3">
          grep<span className="text-emerald-600 dark:text-emerald-400">wise</span>
        </Link>
        <nav aria-label="Main" className="flex min-w-0 items-center gap-0.5 overflow-x-auto sm:gap-1">
          {NAV.map(({ href, label }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-md px-1.5 py-1.5 text-[13px] transition-colors sm:px-2.5 sm:text-sm",
                  active ? "bg-muted text-foreground font-medium" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center">
          <SettingsDialog />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Toggle dark mode"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          >
            <Sun className="hidden dark:block" />
            <Moon className="dark:hidden" />
          </Button>
        </div>
      </div>
    </header>
  );
}

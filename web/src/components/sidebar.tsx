"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { GraduationCap, Users, Camera, CalendarCheck } from "lucide-react";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/classes", label: "Classes", icon: GraduationCap },
  { href: "/people", label: "People", icon: Users },
  { href: "/enroll", label: "Enroll", icon: Camera },
  { href: "/attendance", label: "Attendance", icon: CalendarCheck },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-56 shrink-0 border-r border-border bg-card flex flex-col">
      <div className="px-6 py-5 border-b border-border">
        <h1 className="text-lg font-semibold tracking-tight">ClassCheck</h1>
        <p className="text-xs text-muted-foreground mt-0.5">Face-recognition attendance</p>
      </div>
      <nav className="flex-1 py-3 px-2 space-y-1">
        {LINKS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname?.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                active
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="p-4 text-xs text-muted-foreground border-t border-border">
        v0.1.0 · local admin
      </div>
    </aside>
  );
}

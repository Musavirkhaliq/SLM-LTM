"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { Building2, Check, ChevronsUpDown, LogOut, Menu, Monitor, Moon, ShieldCheck, Sun, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useAuth } from "@/components/providers/auth-provider";
import { USE_MOCKS } from "@/lib/config";
import { ROLE_LABELS } from "@/lib/rbac";
import type { Role } from "@/lib/api/types";
import { DEMO_ACCOUNTS } from "@/lib/mocks/demo-accounts";
import { SidebarContent } from "./sidebar";
import { titleForPath } from "./nav";

export function RoleBadge({ role }: { role: Role | null }) {
  if (!role) return null;
  const variant = role === "platform_admin" ? "destructive" : role === "org_admin" ? "default" : role === "ml_engineer" ? "info" : role === "developer" ? "success" : "secondary";
  return <Badge variant={variant}>{ROLE_LABELS[role]}</Badge>;
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  const Icon = !mounted ? Sun : theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Change theme">
          <Icon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="light">
            <Sun className="mr-2 h-4 w-4" /> Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon className="mr-2 h-4 w-4" /> Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor className="mr-2 h-4 w-4" /> System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function OrgSwitcher() {
  const { me, orgId, membership, switchOrg, role } = useAuth();
  if (!me) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="h-9 max-w-[220px] gap-2 px-3" aria-label="Switch organization">
          <Building2 className="text-muted-foreground" />
          <span className="truncate text-sm">{membership?.org_name ?? "Organization"}</span>
          <ChevronsUpDown className="ml-auto h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Organizations</DropdownMenuLabel>
        {me.memberships.map((m) => (
          <DropdownMenuItem
            key={m.org_id}
            onSelect={async () => {
              if (m.org_id === orgId) return;
              try {
                await switchOrg(m.org_id);
                toast.success(`Switched to ${m.org_name}`);
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Could not switch organization");
              }
            }}
          >
            <Building2 />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{m.org_name}</p>
              <p className="text-xs text-muted-foreground">{me.user.is_platform_admin ? ROLE_LABELS.platform_admin : ROLE_LABELS[m.role]}</p>
            </div>
            {m.org_id === orgId && <Check className="text-primary" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <p className="px-2 py-1.5 text-xs text-muted-foreground">
          Acting as <span className="font-medium text-foreground">{role ? ROLE_LABELS[role] : "—"}</span>
        </p>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function UserMenu() {
  const { me, role, login, logout } = useAuth();
  if (!me) return null;
  const initials = me.user.display_name
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="h-9 gap-2 rounded-full px-1.5" aria-label="User menu">
          <Avatar>
            <AvatarFallback>{initials}</AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <div className="px-2 py-2">
          <p className="truncate text-sm font-medium">{me.user.display_name}</p>
          <p className="truncate text-xs text-muted-foreground">{me.user.email}</p>
          <div className="mt-2">
            <RoleBadge role={role} />
          </div>
        </div>
        {USE_MOCKS && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Demo: view as role</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={me.user.email}
              onValueChange={async (email) => {
                try {
                  await login(email, "demo");
                  toast.success(`Now signed in as ${email}`);
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Could not switch demo user");
                }
              }}
            >
              {DEMO_ACCOUNTS.map((a) => (
                <DropdownMenuRadioItem key={a.email} value={a.email}>
                  <ShieldCheck className="mr-2 h-4 w-4 text-muted-foreground" /> {a.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled>
          <UserRound /> Account settings
        </DropdownMenuItem>
        <DropdownMenuItem destructive onSelect={() => void logout()}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function Topbar() {
  const pathname = usePathname();
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => setOpen(false), [pathname]);
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-6">
      <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}>
        <Menu />
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="border-sidebar-border bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Primary navigation</SheetDescription>
          <SidebarContent onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
      <h2 className="text-sm font-semibold text-muted-foreground">{titleForPath(pathname)}</h2>
      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        {USE_MOCKS && (
          <Badge variant="warning" className="hidden sm:inline-flex">
            Mock data
          </Badge>
        )}
        <OrgSwitcher />
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}

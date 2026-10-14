"use client";

import * as React from "react";
import { useAuth } from "@/components/providers/auth-provider";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { can, requiredRoleLabel, type Capability } from "@/lib/rbac";
import type { Role } from "@/lib/api/types";

interface GateViewProps {
  role: Role | null;
  capability: Capability;
  /** "hide" removes the children entirely; "disable" renders them disabled with an explanatory tooltip. */
  mode?: "hide" | "disable";
  fallback?: React.ReactNode;
  children: React.ReactElement<{ disabled?: boolean }>;
}

/** Pure, testable gating logic (the context-aware `Gate` below wraps this). */
export function GateView({ role, capability, mode = "disable", fallback = null, children }: GateViewProps) {
  if (can(role, capability)) return children;
  if (mode === "hide") return <>{fallback}</>;
  const reason = `Requires the ${requiredRoleLabel(capability)} role or higher`;
  const disabled = React.cloneElement(children, { disabled: true, "aria-disabled": true } as Partial<{ disabled: boolean }>);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* disabled buttons don't fire pointer events, so the tooltip hangs off a focusable wrapper */}
        <span tabIndex={0} className="inline-flex" data-testid="gate-disabled" aria-label={reason}>
          {disabled}
        </span>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  );
}

export function Gate(props: Omit<GateViewProps, "role">) {
  const { role } = useAuth();
  return <GateView role={role} {...props} />;
}

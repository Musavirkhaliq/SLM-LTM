import type { Role } from "@/lib/api/types";

/** Ordered lowest -> highest privilege (CONTRACT.md §2). */
export const ROLE_ORDER: Role[] = ["viewer", "developer", "ml_engineer", "org_admin", "platform_admin"];

export const ROLE_LABELS: Record<Role, string> = {
  viewer: "Viewer",
  developer: "Developer",
  ml_engineer: "ML Engineer",
  org_admin: "Org Admin",
  platform_admin: "Platform Admin",
};

/**
 * Capability -> minimum role. Mirrors the permission matrix in CONTRACT.md §2:
 *  - read:            read models/deployments/evals/usage                    (viewer)
 *  - inference:       call inference, playground, project-level keys         (developer)
 *  - manage_ml:       register models, datasets, fine-tune, evaluate, RAG    (ml_engineer)
 *  - manage_deploy:   create/scale/stop deployments, endpoints & routes      (ml_engineer)
 *  - org_admin:       org-level keys, members, quotas, SSO, audit, cost      (org_admin)
 *  - platform_admin:  global catalogue, all orgs, platform settings          (platform_admin)
 */
export const CAPABILITIES = {
  read: "viewer",
  inference: "developer",
  manage_ml: "ml_engineer",
  manage_deploy: "ml_engineer",
  org_admin: "org_admin",
  platform_admin: "platform_admin",
} as const satisfies Record<string, Role>;

export type Capability = keyof typeof CAPABILITIES;

export function roleRank(role: Role | null | undefined): number {
  return role ? ROLE_ORDER.indexOf(role) : -1;
}

export function hasRole(role: Role | null | undefined, min: Role): boolean {
  return roleRank(role) >= roleRank(min);
}

export function can(role: Role | null | undefined, capability: Capability): boolean {
  return hasRole(role, CAPABILITIES[capability]);
}

export function requiredRoleLabel(capability: Capability): string {
  return ROLE_LABELS[CAPABILITIES[capability]];
}

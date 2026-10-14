/** Demo personas available when the mock layer is active (one per RBAC role). */
export const DEMO_ACCOUNTS = [
  { email: "admin@acme.io", label: "Org Admin", role: "org_admin" },
  { email: "ml@acme.io", label: "ML Engineer", role: "ml_engineer" },
  { email: "dev@acme.io", label: "Developer", role: "developer" },
  { email: "viewer@acme.io", label: "Viewer", role: "viewer" },
  { email: "platform@slm.studio", label: "Platform Admin", role: "platform_admin" },
] as const;

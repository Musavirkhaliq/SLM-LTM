"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, apiClient } from "@/lib/api";
import { decodeJwt } from "@/lib/api/token";
import type { Me, Membership, Role } from "@/lib/api/types";
import { can as canDo, type Capability } from "@/lib/rbac";

type Status = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: Status;
  me: Me | null;
  role: Role | null;
  orgId: string | null;
  membership: Membership | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchOrg: (orgId: string) => Promise<void>;
  can: (capability: Capability) => boolean;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

interface Claims extends Record<string, unknown> {
  org?: string;
  role?: Role;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = React.useState<Status>("loading");
  const [me, setMe] = React.useState<Me | null>(null);
  const [claims, setClaims] = React.useState<Claims | null>(null);

  const syncClaims = React.useCallback(() => {
    const t = apiClient.tokens.getAccessToken();
    setClaims(t ? decodeJwt<Claims>(t) : null);
  }, []);

  React.useEffect(() => apiClient.tokens.subscribe(syncClaims), [syncClaims]);

  const loadMe = React.useCallback(async () => {
    const data = await api.auth.me();
    setMe(data);
    syncClaims();
    setStatus("authenticated");
  }, [syncClaims]);

  // Restore a session from the persisted refresh token on first load.
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!apiClient.tokens.getRefreshToken()) {
        setStatus("unauthenticated");
        return;
      }
      const ok = await apiClient.refresh();
      if (cancelled) return;
      if (!ok) {
        setStatus("unauthenticated");
        return;
      }
      try {
        await loadMe();
      } catch {
        if (!cancelled) setStatus("unauthenticated");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  // Refresh failed mid-session -> back to the login screen.
  React.useEffect(() => {
    apiClient.onUnauthorized = () => {
      setMe(null);
      setStatus("unauthenticated");
      qc.clear();
    };
    return () => {
      apiClient.onUnauthorized = undefined;
    };
  }, [qc]);

  const login = React.useCallback(
    async (email: string, password: string) => {
      const pair = await api.auth.login(email, password);
      apiClient.tokens.set(pair);
      qc.clear();
      await loadMe();
    },
    [loadMe, qc],
  );

  const logout = React.useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      /* best effort */
    }
    apiClient.tokens.clear();
    qc.clear();
    setMe(null);
    setStatus("unauthenticated");
  }, [qc]);

  const switchOrg = React.useCallback(
    async (orgId: string) => {
      const pair = await api.auth.switchOrg(orgId);
      apiClient.tokens.set(pair);
      await loadMe();
      await qc.invalidateQueries();
    },
    [loadMe, qc],
  );

  const orgId = (claims?.org as string | undefined) ?? me?.active_org_id ?? null;
  const membership = me?.memberships.find((m) => m.org_id === orgId) ?? null;
  const role: Role | null = (claims?.role as Role | undefined) ?? membership?.role ?? null;

  const value = React.useMemo<AuthContextValue>(
    () => ({ status, me, role, orgId, membership, login, logout, switchOrg, can: (c) => canDo(role, c) }),
    [status, me, role, orgId, membership, login, logout, switchOrg],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

/** Convenience hook: `const canDeploy = useCan("manage_deploy")`. */
export function useCan(capability: Capability): boolean {
  const { role } = useAuth();
  return canDo(role, capability);
}

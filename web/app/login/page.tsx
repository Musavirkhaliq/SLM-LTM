"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Cpu, Gauge, Layers, ShieldCheck } from "lucide-react";
import { Logo } from "@/components/layout/sidebar";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/common/page";
import { api } from "@/lib/api";
import { USE_MOCKS, API_BASE } from "@/lib/config";
import { DEMO_ACCOUNTS } from "@/lib/mocks/demo-accounts";

const SSO = [
  { id: "azure_ad", label: "Microsoft" },
  { id: "google", label: "Google" },
  { id: "github", label: "GitHub" },
] as const;

export default function LoginPage() {
  const { status, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const goNext = React.useCallback(() => {
    const next = new URLSearchParams(window.location.search).get("next");
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
  }, [router]);

  React.useEffect(() => {
    if (status === "authenticated") goNext();
  }, [status, goNext]);

  async function submit(e: React.FormEvent, creds?: { email: string; password: string }) {
    e.preventDefault();
    const em = creds?.email ?? email;
    const pw = creds?.password ?? password;
    setError(null);
    if (!em || !pw) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    try {
      await login(em, pw);
      goNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  function sso(provider: (typeof SSO)[number]["id"]) {
    if (USE_MOCKS) {
      setError(`SSO with ${provider} is simulated in demo mode - use a demo account below.`);
      return;
    }
    window.location.href = `${API_BASE}${api.auth.oauthStartUrl(provider)}`;
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-gradient-to-br from-indigo-600 via-indigo-700 to-violet-800 p-10 text-white lg:flex">
        <div className="absolute inset-0 opacity-20 [background:radial-gradient(circle_at_20%_20%,white,transparent_40%),radial-gradient(circle_at_80%_70%,#a78bfa,transparent_45%)]" aria-hidden />
        <div className="relative flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/15 text-sm font-bold backdrop-blur">S</span>
          <span className="text-[15px] font-semibold tracking-tight">SLM Studio</span>
        </div>
        <div className="relative max-w-md">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">The platform for your own small language models.</h1>
          <p className="mt-3 text-indigo-100">Fine-tune, evaluate, deploy and serve Phi, Llama, Gemma, Qwen and Mistral behind one OpenAI-compatible API.</p>
          <ul className="mt-8 space-y-4 text-sm">
            {[
              [Layers, "Registry, fine-tuning and evaluation in one workflow"],
              [Cpu, "vLLM serving with autoscaling on your GPUs or cloud"],
              [Gauge, "Live latency, throughput and cost visibility"],
              [ShieldCheck, "RBAC, audit logs and tenant isolation by default"],
            ].map(([Icon, text], i) => {
              const I = Icon as typeof Layers;
              return (
                <li key={i} className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-white/10">
                    <I className="h-4 w-4" aria-hidden />
                  </span>
                  {text as string}
                </li>
              );
            })}
          </ul>
        </div>
        <p className="relative text-xs text-indigo-200">© {new Date().getFullYear()} SLM Studio</p>
      </div>

      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <Logo className="mb-8 lg:hidden" />
          <h2 className="text-2xl font-semibold tracking-tight">Sign in</h2>
          <p className="mt-1 text-sm text-muted-foreground">Welcome back. Sign in to your workspace.</p>

          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            <Field label="Email" htmlFor="email">
              <Input id="email" type="email" autoComplete="username" placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Password" htmlFor="password">
              <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {error && (
              <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" loading={busy}>
              Sign in
            </Button>
          </form>

          <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" /> or continue with <span className="h-px flex-1 bg-border" />
          </div>
          <div className="grid grid-cols-3 gap-2">
            {SSO.map((p) => (
              <Button key={p.id} type="button" variant="outline" size="sm" onClick={() => sso(p.id)}>
                {p.label}
              </Button>
            ))}
          </div>

          {USE_MOCKS && (
            <div className="mt-8 rounded-lg border bg-muted/40 p-4">
              <p className="text-xs font-medium">Demo accounts (any password)</p>
              <p className="mt-0.5 text-xs text-muted-foreground">Each persona has a different role, so the UI shows different actions.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {DEMO_ACCOUNTS.map((a) => (
                  <Button
                    key={a.email}
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={(e) => submit(e, { email: a.email, password: "demo" })}
                  >
                    {a.label}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

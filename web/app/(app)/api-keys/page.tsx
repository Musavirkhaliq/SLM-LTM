"use client";

import * as React from "react";
import { AlertTriangle, KeyRound, MoreHorizontal, Plus, RefreshCw, ShieldOff } from "lucide-react";
import { toast } from "sonner";
import { Sparkline } from "@/components/common/charts";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { CopyButton } from "@/components/common/copy-button";
import { EmptyState, ErrorState, Field, PageHeader, TableSkeleton } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { useAuth } from "@/components/providers/auth-provider";
import { Gate } from "@/components/rbac/gate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiKeys, useApiKeyUsage, useCreateApiKey, useProjects, useRevokeApiKey, useRotateApiKey } from "@/lib/api/hooks";
import type { ApiKey, ApiKeyCreated } from "@/lib/api/types";
import { formatDate, formatNumber, timeAgo } from "@/lib/utils";

function keyStatus(k: ApiKey, now = Date.now()): "revoked" | "expired" | "rotating" | "active" {
  if (k.revoked_at) return "revoked";
  if (k.expires_at && Date.parse(k.expires_at) < now) return "expired";
  if (k.grace_until && Date.parse(k.grace_until) > now) return "rotating";
  return "active";
}

function UsageCell({ k }: { k: ApiKey }) {
  const usage = useApiKeyUsage(k.id);
  if (k.revoked_at) return <span className="text-muted-foreground">—</span>;
  if (usage.isLoading) return <Skeleton className="h-6 w-28" />;
  if (!usage.data) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex w-36 items-center gap-2">
      <Sparkline data={usage.data.points.map((p) => p.requests)} height={26} className="w-20" label={`Requests over the last 24 hours for ${k.name}`} />
      <span className="text-xs tabular-nums text-muted-foreground">{formatNumber(usage.data.total_requests, 0)}</span>
    </div>
  );
}

function SecretDialog({ secret, onClose }: { secret: ApiKeyCreated | null; onClose: () => void }) {
  const [saved, setSaved] = React.useState(false);
  React.useEffect(() => setSaved(false), [secret?.id]);
  return (
    <Dialog open={!!secret} onOpenChange={(o) => !o && saved && onClose()}>
      <DialogContent
        hideClose
        className="max-w-lg"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => {
          if (!saved) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>Save your API key</DialogTitle>
          <DialogDescription>
            This is the only time the full secret is shown. Store it somewhere safe - we keep only a hash and cannot show it again.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex items-center gap-1 rounded-md border bg-muted px-3 py-2">
            <code className="min-w-0 flex-1 break-all font-mono text-xs" data-testid="api-key-secret">
              {secret?.key}
            </code>
            <CopyButton value={secret?.key ?? ""} label="Copy API key" />
          </div>
          <p className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
            Treat this key like a password. Never commit it to source control or expose it in client-side code.
          </p>
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <Checkbox checked={saved} onCheckedChange={(c) => setSaved(c === true)} aria-label="I have copied and stored this key" />
            I have copied and stored this key
          </label>
        </div>
        <DialogFooter>
          <Button disabled={!saved} onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const EXPIRY: Record<string, { label: string; days: number | null }> = {
  never: { label: "Never", days: null },
  "30": { label: "30 days", days: 30 },
  "90": { label: "90 days", days: 90 },
  "365": { label: "1 year", days: 365 },
};

function CreateDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (k: ApiKeyCreated) => void }) {
  const { can } = useAuth();
  const projects = useProjects();
  const create = useCreateApiKey();
  const canOrg = can("org_admin");
  const [name, setName] = React.useState("");
  const [scope, setScope] = React.useState<"project" | "organization">("project");
  const [projectId, setProjectId] = React.useState("");
  const [rpm, setRpm] = React.useState("");
  const [expiry, setExpiry] = React.useState("never");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setName("");
      setScope("project");
      setRpm("");
      setExpiry("never");
      setError(null);
    }
  }, [open]);
  React.useEffect(() => {
    if (open && !projectId && projects.data?.length) setProjectId(projects.data[0]!.id);
  }, [open, projectId, projects.data]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim().length < 2) return setError("Give the key a descriptive name (at least 2 characters).");
    if (scope === "project" && !projectId) return setError("Select a project for this key.");
    if (rpm && (!/^\d+$/.test(rpm) || Number(rpm) < 1)) return setError("Rate limit must be a positive whole number.");
    try {
      const days = EXPIRY[expiry]?.days;
      const key = await create.mutateAsync({
        name: name.trim(),
        scope,
        project_id: scope === "project" ? projectId : null,
        rate_limit_rpm: rpm ? Number(rpm) : null,
        expires_at: days ? new Date(Date.now() + days * 86_400_000).toISOString() : null,
      });
      onOpenChange(false);
      onCreated(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the key");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create API key</DialogTitle>
          <DialogDescription>Keys authenticate calls to the OpenAI-compatible inference API.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <Field label="Name" htmlFor="key-name">
            <Input id="key-name" placeholder="e.g. support-bot-prod" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Scope</legend>
            <RadioGroup value={scope} onValueChange={(v) => setScope(v as "project" | "organization")} aria-label="Key scope">
              <div className="flex items-start gap-2.5 rounded-md border p-3">
                <RadioGroupItem value="project" id="scope-project" className="mt-0.5" />
                <Label htmlFor="scope-project" className="cursor-pointer font-normal">
                  <span className="font-medium">Project</span>
                  <span className="block text-xs text-muted-foreground">Limited to one project. Recommended for applications.</span>
                </Label>
              </div>
              <div className={`flex items-start gap-2.5 rounded-md border p-3 ${canOrg ? "" : "opacity-60"}`}>
                <RadioGroupItem value="organization" id="scope-org" className="mt-0.5" disabled={!canOrg} />
                <Label htmlFor="scope-org" className={`font-normal ${canOrg ? "cursor-pointer" : "cursor-not-allowed"}`}>
                  <span className="font-medium">Organization</span>
                  <span className="block text-xs text-muted-foreground">
                    Access to every project. {canOrg ? "Use sparingly." : "Requires the Org Admin role."}
                  </span>
                </Label>
              </div>
            </RadioGroup>
          </fieldset>
          {scope === "project" && (
            <Field label="Project" htmlFor="key-project">
              <Select value={projectId} onValueChange={setProjectId}>
                <SelectTrigger id="key-project">
                  <SelectValue placeholder="Select a project" />
                </SelectTrigger>
                <SelectContent>
                  {projects.data?.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Rate limit (req/min)" htmlFor="key-rpm" hint="Blank uses the org default.">
              <Input id="key-rpm" inputMode="numeric" placeholder="Org default" value={rpm} onChange={(e) => setRpm(e.target.value)} />
            </Field>
            <Field label="Expires" htmlFor="key-expiry">
              <Select value={expiry} onValueChange={setExpiry}>
                <SelectTrigger id="key-expiry">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(EXPIRY).map(([k, v]) => (
                    <SelectItem key={k} value={k}>
                      {v.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Create key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function ApiKeysPage() {
  const { can } = useAuth();
  const keys = useApiKeys();
  const projects = useProjects();
  const rotate = useRotateApiKey();
  const revoke = useRevokeApiKey();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [secret, setSecret] = React.useState<ApiKeyCreated | null>(null);
  const [rotateTarget, setRotateTarget] = React.useState<ApiKey | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<ApiKey | null>(null);
  const projectName = (id: string | null) => projects.data?.find((p) => p.id === id)?.name ?? "Project";
  const canManage = (k: ApiKey) => can(k.scope === "organization" ? "org_admin" : "inference");

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="API keys"
        description="Authenticate applications against the inference API. Secrets are shown once and stored only as hashes."
        actions={
          <Gate capability="inference">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus /> Create key
            </Button>
          </Gate>
        }
      />

      <Card>
        {keys.isLoading ? (
          <TableSkeleton rows={5} cols={6} />
        ) : keys.isError ? (
          <ErrorState error={keys.error} onRetry={() => keys.refetch()} className="m-6" title="Could not load API keys" />
        ) : !keys.data?.length ? (
          <EmptyState
            icon={KeyRound}
            title="No API keys yet"
            description="Create a key to call your deployments from applications."
            className="m-6"
            action={
              <Gate capability="inference">
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus /> Create key
                </Button>
              </Gate>
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Name</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Usage (24h)</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead className="w-[1%]">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {keys.data.map((k) => {
                const status = keyStatus(k);
                const inactive = status === "revoked" || status === "expired";
                return (
                  <TableRow key={k.id} className={inactive ? "opacity-70" : undefined}>
                    <TableCell>
                      <p className="font-medium">{k.name}</p>
                      <p className="text-xs text-muted-foreground">
                        Created {formatDate(k.created_at)}
                        {k.rate_limit_rpm ? ` · ${k.rate_limit_rpm} rpm` : ""}
                      </p>
                    </TableCell>
                    <TableCell>
                      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{k.prefix}…</code>
                    </TableCell>
                    <TableCell>
                      {k.scope === "organization" ? <Badge variant="default">Organization</Badge> : <Badge variant="secondary">{projectName(k.project_id)}</Badge>}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={status} label={status === "rotating" ? "Rotating (grace)" : undefined} />
                      {k.expires_at && status === "active" && <p className="mt-1 text-[11px] text-muted-foreground">expires {formatDate(k.expires_at)}</p>}
                      {status === "rotating" && k.grace_until && <p className="mt-1 text-[11px] text-muted-foreground">old key valid until {formatDate(k.grace_until)}</p>}
                    </TableCell>
                    <TableCell>
                      <UsageCell k={k} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">{timeAgo(k.last_used_at)}</TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button size="icon-sm" variant="ghost" aria-label={`Actions for ${k.name}`}>
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem disabled={!canManage(k) || !!k.revoked_at} onSelect={() => setRotateTarget(k)}>
                            <RefreshCw /> Rotate
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem destructive disabled={!canManage(k) || !!k.revoked_at} onSelect={() => setRevokeTarget(k)}>
                            <ShieldOff /> Revoke
                          </DropdownMenuItem>
                          {!canManage(k) && <p className="px-2 py-1.5 text-[11px] text-muted-foreground">Your role cannot manage this key.</p>}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <CreateDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={setSecret} />
      <SecretDialog secret={secret} onClose={() => setSecret(null)} />
      <ConfirmDialog
        open={!!rotateTarget}
        onOpenChange={(o) => !o && setRotateTarget(null)}
        title={`Rotate ${rotateTarget?.name}?`}
        description="A new secret is issued immediately. The current key keeps working for a 24-hour grace period so you can roll it out without downtime."
        confirmLabel="Rotate key"
        onConfirm={async () => {
          try {
            const k = await rotate.mutateAsync(rotateTarget!.id);
            setSecret(k);
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Rotation failed");
          }
        }}
      />
      <ConfirmDialog
        open={!!revokeTarget}
        onOpenChange={(o) => !o && setRevokeTarget(null)}
        title={`Revoke ${revokeTarget?.name}?`}
        description="Requests using this key will fail immediately. This cannot be undone."
        confirmLabel="Revoke key"
        destructive
        onConfirm={async () => {
          try {
            await revoke.mutateAsync(revokeTarget!.id);
            toast.success(`Revoked ${revokeTarget!.name}`);
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Revocation failed");
          }
        }}
      />
    </div>
  );
}

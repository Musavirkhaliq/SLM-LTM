"use client";

import * as React from "react";
import { toast } from "sonner";
import { Field } from "@/components/common/page";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCreateModel } from "@/lib/api/hooks";
import type { ModelSource } from "@/lib/api/types";

const FAMILIES = ["phi", "llama", "gemma", "qwen", "mistral", "other"];

export function RegisterModelDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const create = useCreateModel();
  const [name, setName] = React.useState("");
  const [repo, setRepo] = React.useState("");
  const [family, setFamily] = React.useState("phi");
  const [license, setLicense] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [source, setSource] = React.useState<ModelSource>("huggingface");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setName("");
      setRepo("");
      setFamily("phi");
      setLicense("");
      setDescription("");
      setSource("huggingface");
      setError(null);
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}$/.test(name)) return setError("Name must be 2-63 characters: letters, numbers, dots, dashes or underscores.");
    if (source === "huggingface" && !/^[\w.-]+\/[\w.-]+$/.test(repo)) return setError("Enter a Hugging Face repo such as org/model-name.");
    try {
      await create.mutateAsync({
        name,
        family,
        license: license || null,
        description: description || null,
        source,
        hf_repo: source === "huggingface" ? repo : null,
      });
      toast.success(`Registered ${name}`);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not register the model");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Register a model</DialogTitle>
          <DialogDescription>Add a model from Hugging Face or an uploaded artifact to your private registry.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor="rm-name">
              <Input id="rm-name" placeholder="my-model-7b" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Source" htmlFor="rm-source">
              <Select value={source} onValueChange={(v) => setSource(v as ModelSource)}>
                <SelectTrigger id="rm-source">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="huggingface">Hugging Face Hub</SelectItem>
                  <SelectItem value="upload">Uploaded artifact</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
          {source === "huggingface" && (
            <Field label="Hugging Face repository" htmlFor="rm-repo" hint="Gated models require an HF token configured by an admin.">
              <Input id="rm-repo" placeholder="microsoft/phi-4" value={repo} onChange={(e) => setRepo(e.target.value)} />
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Family" htmlFor="rm-family">
              <Select value={family} onValueChange={setFamily}>
                <SelectTrigger id="rm-family">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FAMILIES.map((f) => (
                    <SelectItem key={f} value={f}>
                      {f}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="License" htmlFor="rm-license">
              <Input id="rm-license" placeholder="Apache-2.0" value={license} onChange={(e) => setLicense(e.target.value)} />
            </Field>
          </div>
          <Field label="Description" htmlFor="rm-desc">
            <Textarea id="rm-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
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
              Register model
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

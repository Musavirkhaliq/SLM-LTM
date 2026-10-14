"use client";

import * as React from "react";
import { Plus } from "lucide-react";
import { PageHeader } from "@/components/common/page";
import { Benchmarks } from "@/components/evaluation/benchmarks";
import { Compare } from "@/components/evaluation/compare";
import { Runs } from "@/components/evaluation/runs";
import { EvaluateDialog } from "@/components/models/evaluate-dialog";
import { Gate } from "@/components/rbac/gate";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function EvaluationPage() {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="Evaluation"
        description="Benchmark models, run evaluations on your own datasets and compare candidates on accuracy, cost and latency."
        actions={
          <Gate capability="manage_ml">
            <Button onClick={() => setOpen(true)}>
              <Plus /> New evaluation
            </Button>
          </Gate>
        }
      />
      <Tabs defaultValue="benchmarks">
        <TabsList>
          <TabsTrigger value="benchmarks">Benchmarks</TabsTrigger>
          <TabsTrigger value="compare">Compare A vs B</TabsTrigger>
          <TabsTrigger value="runs">Runs</TabsTrigger>
        </TabsList>
        <TabsContent value="benchmarks">
          <Benchmarks />
        </TabsContent>
        <TabsContent value="compare">
          <Compare />
        </TabsContent>
        <TabsContent value="runs">
          <Runs />
        </TabsContent>
      </Tabs>
      <EvaluateDialog open={open} onOpenChange={setOpen} modelId={null} />
    </div>
  );
}

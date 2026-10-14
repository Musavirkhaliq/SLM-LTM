"use client";

import * as React from "react";
import { PageHeader } from "@/components/common/page";
import { DocumentsTab } from "@/components/rag/documents-tab";
import { IndexesTab } from "@/components/rag/indexes-tab";
import { PipelineBuilder } from "@/components/rag/pipeline-builder";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function RagPage() {
  const [tab, setTab] = React.useState("pipeline");
  const [docIndex, setDocIndex] = React.useState<string | null>(null);
  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="RAG Studio"
        description="Ground your models in your own documents. Design the retrieval pipeline, manage indexes and test retrieval."
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="pipeline">Pipeline builder</TabsTrigger>
          <TabsTrigger value="indexes">Indexes</TabsTrigger>
          <TabsTrigger value="documents">Documents & query</TabsTrigger>
        </TabsList>
        <TabsContent value="pipeline">
          <PipelineBuilder onCreated={() => setTab("indexes")} />
        </TabsContent>
        <TabsContent value="indexes">
          <IndexesTab
            onNew={() => setTab("pipeline")}
            onOpenDocs={(id) => {
              setDocIndex(id);
              setTab("documents");
            }}
          />
        </TabsContent>
        <TabsContent value="documents">
          <DocumentsTab indexId={docIndex} onIndexChange={setDocIndex} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

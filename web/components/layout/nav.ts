import {
  Activity,
  Boxes,
  Database,
  FlaskConical,
  KeyRound,
  LayoutDashboard,
  MessageSquare,
  Rocket,
  SlidersHorizontal,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const NAV: NavSection[] = [
  {
    title: "Overview",
    items: [{ href: "/", label: "Dashboard", icon: LayoutDashboard, description: "Fleet health, usage and spend at a glance" }],
  },
  {
    title: "Build",
    items: [
      { href: "/models", label: "Models", icon: Boxes, description: "Model registry, versions and benchmarks" },
      { href: "/fine-tuning", label: "Fine-tuning", icon: SlidersHorizontal, description: "Train LoRA / QLoRA adapters on your data" },
      { href: "/evaluation", label: "Evaluation", icon: FlaskConical, description: "Benchmark and compare models" },
      { href: "/rag", label: "RAG Studio", icon: Database, description: "Indexes, retrieval and pipelines" },
      { href: "/playground", label: "Playground", icon: MessageSquare, description: "Chat with your models" },
    ],
  },
  {
    title: "Operate",
    items: [
      { href: "/deployments", label: "Deployments", icon: Rocket, description: "Endpoints, scaling and rollouts" },
      { href: "/monitoring", label: "Monitoring", icon: Activity, description: "Live latency, throughput and GPU metrics" },
    ],
  },
  {
    title: "Manage",
    items: [
      { href: "/api-keys", label: "API Keys", icon: KeyRound, description: "Create, rotate and revoke access keys" },
      { href: "/cost", label: "Cost", icon: Wallet, description: "Spend by project, deployment and model" },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV.flatMap((s) => s.items);

export function titleForPath(pathname: string): string {
  const exact = ALL_NAV_ITEMS.find((i) => i.href === pathname);
  if (exact) return exact.label;
  const prefix = ALL_NAV_ITEMS.filter((i) => i.href !== "/").find((i) => pathname.startsWith(i.href));
  return prefix?.label ?? "SLM Studio";
}

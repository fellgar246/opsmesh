"use client";

import { useMemo, useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { layoutTasks } from "@/lib/graph";
import { agentLabels, taskStatusLabels } from "@/lib/labels";
import type { AgentTask } from "@/lib/types";

const nodeWidth = 200;
const nodeHeight = 64;
const columnGap = 48;
const rowGap = 24;

export function TaskGraph({ tasks }: { tasks: AgentTask[] }) {
  const [mode, setMode] = useState<"graph" | "list">("graph");
  const [selected, setSelected] = useState<string | null>(null);
  const layout = useMemo(
    () =>
      layoutTasks(
        tasks.map((task) => ({
          id: task.id,
          agentType: task.agent_type,
          status: task.status,
          dependsOn: task.depends_on,
        })),
      ),
    [tasks],
  );
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const selectedTask = selected ? byId.get(selected) : undefined;
  const columns = Math.max(1, ...layout.nodes.map((node) => node.column + 1));
  const rows = Math.max(1, ...layout.nodes.map((node) => node.row + 1));
  const width = columns * nodeWidth + (columns - 1) * columnGap + 8;
  const height = rows * nodeHeight + (rows - 1) * rowGap + 8;
  const position = new Map(
    layout.nodes.map((node) => [
      node.id,
      { x: node.column * (nodeWidth + columnGap), y: node.row * (nodeHeight + rowGap) },
    ]),
  );

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div>
        <div className="mb-3 flex gap-2">
          <button
            type="button"
            className="h-8 rounded-md border border-line px-3"
            aria-pressed={mode === "graph"}
            onClick={() => setMode("graph")}
          >
            Graph
          </button>
          <button
            type="button"
            className="h-8 rounded-md border border-line px-3"
            aria-pressed={mode === "list"}
            onClick={() => setMode("list")}
          >
            List
          </button>
        </div>
        {mode === "list" ? (
          <ul className="space-y-2">
            {tasks.map((task) => (
              <li key={task.id}>
                <TaskButton task={task} selected={selected === task.id} onSelect={setSelected} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="overflow-auto rounded-lg border border-line bg-sunken p-4">
            <div className="relative" style={{ width, height }}>
              <svg className="absolute inset-0" width={width} height={height} role="img" aria-label="Task graph">
                {layout.edges.map((edge) => {
                  const from = position.get(edge.from);
                  const to = position.get(edge.to);
                  if (!from || !to) return null;
                  const source = byId.get(edge.from);
                  const failed = source?.status === "FAILED";
                  return (
                    <line
                      key={`${edge.from}-${edge.to}`}
                      data-testid="task-edge"
                      x1={from.x + nodeWidth}
                      y1={from.y + nodeHeight / 2}
                      x2={to.x}
                      y2={to.y + nodeHeight / 2}
                      stroke={failed ? "var(--danger-fg)" : "var(--border-strong)"}
                      strokeWidth={1.5}
                    />
                  );
                })}
              </svg>
              {layout.nodes.map((node) => {
                const task = byId.get(node.id);
                if (!task) return null;
                const point = position.get(node.id);
                return (
                  <div
                    key={node.id}
                    className="absolute"
                    style={{ left: point?.x, top: point?.y, width: nodeWidth }}
                  >
                    <TaskButton task={task} selected={selected === task.id} onSelect={setSelected} />
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <aside aria-label="Task inspector" className="rounded-lg border border-line bg-surface p-4">
        {selectedTask ? <TaskInspector task={selectedTask} onClose={() => setSelected(null)} /> : (
          <p className="text-muted">Select a task to inspect its status and failure reason.</p>
        )}
      </aside>
    </div>
  );
}

function TaskButton({
  task,
  selected,
  onSelect,
}: {
  task: AgentTask;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const status = taskStatusLabels[task.status] ?? { label: task.status, tone: "neutral" };
  const failed = task.status === "FAILED";
  return (
    <button
      type="button"
      data-testid={`task-node-${task.agent_type}`}
      aria-pressed={selected}
      onClick={() => onSelect(task.id)}
      className={`flex h-16 w-full items-center gap-2 rounded-lg border bg-surface px-3 text-left ${
        failed ? "border-2 border-[var(--danger-fg)] bg-[var(--danger-bg)]" : "border-line"
      }`}
    >
      <span className="min-w-0">
        <span className="block text-sm font-semibold">
          {agentLabels[task.agent_type] ?? task.agent_type}
        </span>
        <StatusBadge
          status={task.status}
          label={status.label}
          tone={status.tone}
          testId={`task-status-${task.agent_type}`}
        />
      </span>
    </button>
  );
}

function TaskInspector({ task, onClose }: { task: AgentTask; onClose: () => void }) {
  const status = taskStatusLabels[task.status] ?? { label: task.status, tone: "neutral" };
  const warnings = task.results.flatMap((result) => result.warnings);
  const reason =
    warnings.map((warning) => warning.message).join(" ") ||
    (task.status === "FAILED" ? "No failure reason was recorded." : "This task has no failure.");
  return (
    <div>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold">{agentLabels[task.agent_type] ?? task.agent_type}</h3>
        <button type="button" onClick={onClose} aria-label="Close inspector">
          Close
        </button>
      </div>
      <div className="mt-2">
        <StatusBadge status={task.status} label={status.label} tone={status.tone} />
      </div>
      <p data-testid="task-failure-reason" className="mt-3 text-sm">
        {reason}
      </p>
    </div>
  );
}

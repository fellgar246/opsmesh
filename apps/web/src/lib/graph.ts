export type GraphTask = {
  id: string;
  agentType: string;
  status: string;
  dependsOn: string[];
};

export type LayoutNode = GraphTask & {
  column: number;
  row: number;
};

export type LayoutEdge = {
  from: string;
  to: string;
};

export function layoutTasks(tasks: GraphTask[]): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const column = new Map<string, number>();

  function columnOf(id: string, stack: Set<string>): number {
    const known = column.get(id);
    if (known !== undefined) return known;
    if (stack.has(id) || !byId.has(id)) return 0;
    stack.add(id);
    const dependencies = byId.get(id)?.dependsOn.filter((dependency) => byId.has(dependency)) ?? [];
    const value =
      dependencies.length === 0
        ? 0
        : 1 + Math.max(...dependencies.map((dependency) => columnOf(dependency, stack)));
    stack.delete(id);
    column.set(id, value);
    return value;
  }

  for (const task of tasks) columnOf(task.id, new Set());

  const nextRow = new Map<number, number>();
  const nodes = tasks.map((task) => {
    const taskColumn = column.get(task.id) ?? 0;
    const row = nextRow.get(taskColumn) ?? 0;
    nextRow.set(taskColumn, row + 1);
    return { ...task, column: taskColumn, row };
  });

  const edges: LayoutEdge[] = [];
  for (const task of tasks) {
    for (const dependency of task.dependsOn) {
      if (byId.has(dependency)) edges.push({ from: dependency, to: task.id });
    }
  }
  return { nodes, edges };
}

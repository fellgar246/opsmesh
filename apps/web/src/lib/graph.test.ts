import { describe, expect, it } from "vitest";

import { layoutTasks } from "./graph";

describe("task graph layout", () => {
  it("places two independent tasks beside one dependent task", () => {
    const layout = layoutTasks([
      { id: "a", agentType: "inventory", status: "COMPLETED", dependsOn: [] },
      { id: "b", agentType: "demand", status: "COMPLETED", dependsOn: [] },
      { id: "c", agentType: "policy", status: "COMPLETED", dependsOn: ["a", "b"] },
    ]);
    expect(layout.nodes).toHaveLength(3);
    expect(layout.edges).toEqual([
      { from: "a", to: "c" },
      { from: "b", to: "c" },
    ]);
    const inventory = layout.nodes.find((node) => node.id === "a");
    const demand = layout.nodes.find((node) => node.id === "b");
    const policy = layout.nodes.find((node) => node.id === "c");
    expect(inventory?.column).toBe(0);
    expect(demand?.column).toBe(0);
    expect(inventory?.row).not.toBe(demand?.row);
    expect(policy?.column).toBe(1);
  });
});

import { describe, expect, it } from "vitest";

import { featureFlagSchema, parseExecutionProfile } from "./execution-profile";

describe("execution profile", () => {
  it("defaults to local simulation", () => {
    expect(parseExecutionProfile(undefined)).toBe("local");
  });

  it("accepts the named profiles", () => {
    expect(parseExecutionProfile("aws-demo")).toBe("aws-demo");
    expect(parseExecutionProfile("showcase")).toBe("showcase");
  });

  it("keeps expensive flags off by default", () => {
    const flags = featureFlagSchema.parse({});
    expect(flags.managedRetrievalEnabled).toBe(false);
    expect(flags.longTermMemoryEnabled).toBe(false);
    expect(flags.automaticSchedulesEnabled).toBe(false);
  });
});

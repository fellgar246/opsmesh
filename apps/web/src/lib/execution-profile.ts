import { z } from "zod";

export const executionProfileSchema = z.enum(["local", "aws-demo", "showcase"]).default("local");

export type ExecutionProfile = z.infer<typeof executionProfileSchema>;

export const featureFlagSchema = z.object({
  agenticFeaturesEnabled: z.boolean().default(false),
  multiAgentExecutionEnabled: z.boolean().default(false),
  cloudAgentRuntimeEnabled: z.boolean().default(false),
  managedRetrievalEnabled: z.boolean().default(false),
  longTermMemoryEnabled: z.boolean().default(false),
  automaticSchedulesEnabled: z.boolean().default(false),
});

export type FeatureFlags = z.infer<typeof featureFlagSchema>;

export function parseExecutionProfile(value: unknown): ExecutionProfile {
  return executionProfileSchema.parse(value);
}

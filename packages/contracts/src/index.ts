import { z } from "zod";

export const PrincipalSchema = z.object({
  id: z.string().min(1),
  groups: z.array(z.string().min(1)).default([]),
  projects: z.array(z.string().min(1)).default([]),
  roles: z.array(z.string().min(1)).default([]),
});
export type Principal = z.infer<typeof PrincipalSchema>;

export const CitationSchema = z.object({
  repository: z.string(),
  path: z.string(),
  revision: z.string(),
  heading: z.string().optional(),
});

export const KnowledgeSearchRequestSchema = z.object({
  query: z.string().min(1).max(2_000),
  project: z.string().min(1),
  limit: z.number().int().min(1).max(50).default(8),
});
export type KnowledgeSearchRequest = z.infer<typeof KnowledgeSearchRequestSchema>;

export const KnowledgeResultSchema = z.object({
  id: z.string(),
  title: z.string(),
  excerpt: z.string(),
  score: z.number(),
  authority: z.enum(["approved", "proposed", "superseded"]),
  citation: CitationSchema,
});
export type KnowledgeResult = z.infer<typeof KnowledgeResultSchema>;

export const KnowledgeSearchResponseSchema = z.object({
  results: z.array(KnowledgeResultSchema),
  requestId: z.string(),
});

export const ErrorResponseSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.record(z.string(), z.unknown()).optional(),
    requestId: z.string(),
  }),
});

export const JobStatusSchema = z.enum([
  "queued", "running", "needs_input", "awaiting_approval", "completed",
  "failed", "timed_out", "cancelled", "no_fix_found", "unsafe_to_proceed",
]);
export type JobStatus = z.infer<typeof JobStatusSchema>;

export const CodingJobRequestSchema = z.object({
  version: z.literal("1"),
  runId: z.string(),
  repository: z.string(),
  baseRevision: z.string(),
  objective: z.string(),
  contextRefs: z.array(z.string()).default([]),
  policy: z.object({
    mayEditFiles: z.boolean(),
    mayRunCommands: z.boolean(),
    mayPushBranch: z.boolean(),
    mayCreateDraftPullRequest: z.boolean(),
    mayMerge: z.literal(false),
    mayDeploy: z.literal(false),
    maxDurationSeconds: z.number().int().positive().max(7_200),
  }),
  harness: z.string(),
  skill: z.string(),
});

export const MemoryProposalSchema = z.object({
  id: z.string(),
  project: z.string(),
  content: z.string().min(1),
  evidence: z.array(CitationSchema).min(1),
  authorId: z.string(),
  status: z.enum(["proposed", "approved", "rejected"]),
  createdAt: z.string().datetime(),
  expiresAt: z.string().datetime().optional(),
  supersedes: z.string().optional(),
});

export const AutomationSchema = z.object({
  id: z.string(),
  enabled: z.boolean(),
  project: z.string(),
  schedule: z.object({
    kind: z.literal("interval"),
    everySeconds: z.number().int().min(60),
    timezone: z.string(),
    nextRunAt: z.string().datetime(),
  }),
  concurrencyLimit: z.number().int().min(1).max(100),
  costLimitUsd: z.number().nonnegative(),
});

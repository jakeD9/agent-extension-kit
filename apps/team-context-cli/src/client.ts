import {
  KnowledgeSearchResponseSchema,
  type KnowledgeSearchRequest,
  type KnowledgeResult,
} from "@agent-extension-kit/contracts";
import { z } from "zod";

const SkillSummarySchema = z.object({
  name: z.string(),
  description: z.string(),
  path: z.string(),
  projects: z.array(z.string()),
  accessGroups: z.array(z.string()),
});
const SkillSchema = SkillSummarySchema.extend({ body: z.string(), requestId: z.string() });

export type SkillSummary = z.infer<typeof SkillSummarySchema>;

export class ContextClientError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

export class ContextClient {
  constructor(private readonly baseUrl: string, private readonly token: string) {}

  private async request(path: string, init?: RequestInit): Promise<unknown> {
    const response = await fetch(new URL(path, this.baseUrl), {
      ...init,
      headers: { authorization: `Bearer ${this.token}`, "content-type": "application/json", ...init?.headers },
    });
    const body = await response.json() as { error?: { code?: string; message?: string } };
    if (!response.ok) {
      throw new ContextClientError(response.status, body.error?.code ?? "request_failed", body.error?.message ?? "Request failed");
    }
    return body;
  }

  async searchKnowledge(request: KnowledgeSearchRequest): Promise<{ results: KnowledgeResult[]; requestId: string }> {
    return KnowledgeSearchResponseSchema.parse(await this.request("/v1/knowledge/search", { method: "POST", body: JSON.stringify(request) }));
  }

  async listSkills(project: string): Promise<{ items: SkillSummary[]; nextCursor?: string; requestId: string }> {
    const parsed = z.object({ items: z.array(SkillSummarySchema), nextCursor: z.string().optional(), requestId: z.string() })
      .parse(await this.request(`/v1/skills?project=${encodeURIComponent(project)}`));
    return parsed.nextCursor
      ? { items: parsed.items, nextCursor: parsed.nextCursor, requestId: parsed.requestId }
      : { items: parsed.items, requestId: parsed.requestId };
  }

  async getSkill(name: string, project: string): Promise<z.infer<typeof SkillSchema>> {
    return SkillSchema.parse(await this.request(`/v1/skills/${encodeURIComponent(name)}?project=${encodeURIComponent(project)}`));
  }
}

import Fastify, { type FastifyBaseLogger } from "fastify";
import type { Authenticator } from "@agent-extension-kit/auth";
import { KnowledgeSearchRequestSchema } from "@agent-extension-kit/contracts";
import { InMemoryKnowledgeIndex, type KnowledgeIndex } from "@agent-extension-kit/context-core";
import type { ContentPack } from "./content-pack.js";

export interface AppDependencies {
  pack: ContentPack;
  authenticator: Authenticator;
  knowledgeIndex?: KnowledgeIndex;
  readiness?: () => Promise<{ status: "ready" | "degraded"; details?: Record<string, unknown> }>;
  logger?: boolean | FastifyBaseLogger;
}

export function buildApp({ pack, authenticator, knowledgeIndex, readiness, logger = true }: AppDependencies) {
  const app = Fastify({ logger });
  const index = knowledgeIndex ?? new InMemoryKnowledgeIndex(pack.chunks);

  app.get("/health", async () => ({ status: "healthy" }));
  app.get("/ready", async (_request, reply) => {
    const state = readiness ? await readiness() : { status: "ready" as const };
    if (state.status === "degraded") reply.code(503);
    return { ...state, contentPack: pack.manifest.id, chunks: pack.chunks.length };
  });

  app.get("/v1/skills", async (request, reply) => {
    const principal = await authenticator.authenticate(request.headers.authorization);
    if (!principal) {
      return reply.code(401).send({ error: { code: "unauthorized", message: "A valid bearer token is required", requestId: request.id } });
    }
    const query = request.query as { project?: string; limit?: string; cursor?: string };
    if (!query.project || !principal.projects.includes(query.project)) return { items: [], requestId: request.id };
    const limit = Math.min(Math.max(Number(query.limit ?? 50) || 50, 1), 50);
    const offset = query.cursor ? Number(Buffer.from(query.cursor, "base64url").toString("utf8")) || 0 : 0;
    const allowed = pack.skills.filter((skill) =>
      skill.projects.includes(query.project!) && skill.accessGroups.some((group) => principal.groups.includes(group))
    );
    const items = allowed.slice(offset, offset + limit).map(({ body: _body, ...skill }) => skill);
    const nextOffset = offset + items.length;
    return {
      items,
      ...(nextOffset < allowed.length ? { nextCursor: Buffer.from(String(nextOffset)).toString("base64url") } : {}),
      requestId: request.id,
    };
  });

  app.get("/v1/skills/:name", async (request, reply) => {
    const principal = await authenticator.authenticate(request.headers.authorization);
    if (!principal) {
      return reply.code(401).send({ error: { code: "unauthorized", message: "A valid bearer token is required", requestId: request.id } });
    }
    const { name } = request.params as { name: string };
    const { project } = request.query as { project?: string };
    const skill = pack.skills.find((candidate) => candidate.name === name
      && !!project
      && principal.projects.includes(project)
      && candidate.projects.includes(project)
      && candidate.accessGroups.some((group) => principal.groups.includes(group)));
    if (!skill) {
      return reply.code(404).send({ error: { code: "skill_not_found", message: "Skill not found", requestId: request.id } });
    }
    return { ...skill, requestId: request.id };
  });

  app.post("/v1/knowledge/search", async (request, reply) => {
    const principal = await authenticator.authenticate(request.headers.authorization);
    if (!principal) {
      return reply.code(401).send({
        error: { code: "unauthorized", message: "A valid bearer token is required", requestId: request.id },
      });
    }
    const parsed = KnowledgeSearchRequestSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(422).send({
        error: {
          code: "validation_failed",
          message: "The search request is invalid",
          details: { issues: parsed.error.issues },
          requestId: request.id,
        },
      });
    }
    const results = await index.search(parsed.data, principal);
    request.log.info({
      event: "knowledge-search-completed",
      requestId: request.id,
      principalId: principal.id,
      project: parsed.data.project,
      resultCount: results.length,
    });
    return { results, requestId: request.id };
  });

  app.setErrorHandler((error, request, reply) => {
    request.log.error({ event: "request-failed", requestId: request.id, err: error });
    return reply.code(500).send({
      error: { code: "internal_error", message: "The request could not be completed", requestId: request.id },
    });
  });

  return app;
}

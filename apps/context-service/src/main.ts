import { resolve } from "node:path";
import { StaticBearerAuthenticator } from "@agent-extension-kit/auth";
import { connectDatabase, DEFAULT_DATABASE_NAME, syncKnowledgeChunks } from "@agent-extension-kit/database";
import { buildApp } from "./app.js";
import { loadContentPack } from "./content-pack.js";

const contentPath = resolve(process.env.CONTENT_PATH ?? "extension");
const revision = process.env.CONTENT_REVISION ?? "working-tree";
const port = Number(process.env.HTTP_PORT ?? 3000);
const principalJson = process.env.DEV_AUTH_PRINCIPALS ?? JSON.stringify({
  "dev-token": { id: "local-developer", groups: ["engineering"], projects: ["event-ingestion"], roles: ["approver"] },
});

const pack = await loadContentPack(contentPath, revision);
const mongo = process.env.MONGODB_URI
  ? await connectDatabase(process.env.MONGODB_URI, process.env.MONGODB_DATABASE ?? DEFAULT_DATABASE_NAME)
  : undefined;
if (mongo) await syncKnowledgeChunks(mongo.db, pack.manifest.id, pack.chunks);
const app = buildApp({
  pack,
  authenticator: StaticBearerAuthenticator.fromJson(principalJson),
  ...(mongo ? {
    knowledgeIndex: mongo.index,
    readiness: async () => {
      try {
        await mongo.db.command({ ping: 1 });
        return { status: "ready" as const };
      } catch (error) {
        return {
          status: "degraded" as const,
          details: { dependency: "mongodb", message: error instanceof Error ? error.message : "unknown" },
        };
      }
    },
  } : {}),
});

const shutdown = async (signal: string) => {
  app.log.info({ event: "shutdown-requested", signal });
  await app.close();
  if (mongo) await mongo.client.close();
  process.exit(0);
};
process.once("SIGTERM", () => void shutdown("SIGTERM"));
process.once("SIGINT", () => void shutdown("SIGINT"));

await app.listen({ host: "0.0.0.0", port });

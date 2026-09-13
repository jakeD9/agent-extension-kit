import type { Collection, Db, Filter, MongoClient } from "mongodb";
import { MongoClient as Client } from "mongodb";
import type { KnowledgeResult, KnowledgeSearchRequest, Principal } from "@agent-extension-kit/contracts";
import { InMemoryKnowledgeIndex, type KnowledgeChunk, type KnowledgeIndex } from "@agent-extension-kit/context-core";

export const DEFAULT_DATABASE_NAME = "agent_extension_kit";

export const COLLECTIONS = {
  knowledgeDocuments: "knowledge_documents",
  knowledgeChunks: "knowledge_chunks",
  knowledgeSourceRevisions: "knowledge_source_revisions",
  skills: "skills",
  memories: "memories",
  memoryProposals: "memory_proposals",
  contextAuditEvents: "context_audit_events",
  slackThreads: "slack_threads",
  conversations: "conversations",
  agentRuns: "agent_runs",
  approvals: "approvals",
  eventReceipts: "event_receipts",
  automations: "automations",
  automationRuns: "automation_runs",
  codeReviews: "code_reviews",
} as const;

export function buildAuthorizedChunkFilter(project: string, groups: string[]): Filter<KnowledgeChunk> {
  return { project, accessGroups: { $in: groups }, authority: "approved" };
}

export class MongoKnowledgeIndex implements KnowledgeIndex {
  constructor(private readonly chunks: Collection<KnowledgeChunk>) {}

  async search(request: KnowledgeSearchRequest, principal: Principal): Promise<KnowledgeResult[]> {
    if (!principal.projects.includes(request.project) || principal.groups.length === 0) return [];
    const candidates = await this.chunks
      .find(buildAuthorizedChunkFilter(request.project, principal.groups))
      .limit(2_000)
      .toArray();
    return new InMemoryKnowledgeIndex(candidates).search(request, principal);
  }
}

async function ensureCollections(db: Db): Promise<void> {
  const names = new Set((await db.listCollections({}, { nameOnly: true }).toArray()).map(({ name }) => name));
  if (!names.has(COLLECTIONS.knowledgeChunks)) {
    await db.createCollection(COLLECTIONS.knowledgeChunks, {
      validator: {
        $jsonSchema: {
          bsonType: "object",
          required: ["id", "project", "accessGroups", "authority", "title", "body", "citation"],
          properties: {
            id: { bsonType: "string" },
            project: { bsonType: "string" },
            accessGroups: { bsonType: "array", items: { bsonType: "string" } },
            authority: { enum: ["approved", "proposed", "superseded"] },
          },
        },
      },
    });
  }
  const chunks = db.collection<KnowledgeChunk>(COLLECTIONS.knowledgeChunks);
  await chunks.createIndex({ id: 1 }, { unique: true });
  await chunks.createIndex({ project: 1, accessGroups: 1, authority: 1 });
  await chunks.createIndex({ "citation.revision": 1 });
}

export async function connectDatabase(uri: string, databaseName = DEFAULT_DATABASE_NAME): Promise<{
  client: MongoClient;
  db: Db;
  index: MongoKnowledgeIndex;
}> {
  const client = new Client(uri);
  await client.connect();
  const db = client.db(databaseName);
  await ensureCollections(db);
  return { client, db, index: new MongoKnowledgeIndex(db.collection<KnowledgeChunk>(COLLECTIONS.knowledgeChunks)) };
}

export async function syncKnowledgeChunks(db: Db, packId: string, chunks: readonly KnowledgeChunk[]): Promise<void> {
  const collection = db.collection<KnowledgeChunk>(COLLECTIONS.knowledgeChunks);
  if (chunks.length > 0) {
    await collection.bulkWrite(chunks.map((chunk) => ({
      replaceOne: { filter: { id: chunk.id }, replacement: chunk, upsert: true },
    })));
  }
  const currentIds = chunks.map(({ id }) => id);
  const escaped = packId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  await collection.deleteMany({
    id: { $regex: `^${escaped}:`, ...(currentIds.length ? { $nin: currentIds } : {}) },
  });
}

import { describe, expect, it } from "vitest";
import { buildAuthorizedChunkFilter, COLLECTIONS, DEFAULT_DATABASE_NAME } from "./index.js";

it("uses one shared database by default", () => {
  expect(DEFAULT_DATABASE_NAME).toBe("agent_extension_kit");
  expect(COLLECTIONS.knowledgeChunks).toBe("knowledge_chunks");
  expect(COLLECTIONS.agentRuns).toBe("agent_runs");
});

describe("Mongo knowledge authorization", () => {
  it("includes project, group, and approved authority in the database filter", () => {
    expect(buildAuthorizedChunkFilter("event-ingestion", ["engineering", "on-call"])).toEqual({
      project: "event-ingestion",
      accessGroups: { $in: ["engineering", "on-call"] },
      authority: "approved",
    });
  });
});

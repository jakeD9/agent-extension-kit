import { describe, expect, it } from "vitest";
import { InMemoryKnowledgeIndex, type KnowledgeChunk } from "./index.js";

const chunks: KnowledgeChunk[] = [
  {
    id: "approved",
    project: "event-ingestion",
    accessGroups: ["engineering"],
    authority: "approved",
    title: "Stable idempotency keys",
    body: "The idempotency key uses the vendor event identifier and never receipt time.",
    citation: { repository: "sample", path: "knowledge/architecture/idempotency.md", revision: "abc123" },
  },
  {
    id: "private",
    project: "billing",
    accessGroups: ["finance"],
    authority: "approved",
    title: "Billing secrets",
    body: "Private billing material",
    citation: { repository: "sample", path: "knowledge/domain/billing.md", revision: "abc123" },
  },
];

describe("InMemoryKnowledgeIndex", () => {
  it("returns a stable citation for an authorized lexical match", async () => {
    const index = new InMemoryKnowledgeIndex(chunks);
    const results = await index.search(
      { query: "vendor idempotency", project: "event-ingestion", limit: 8 },
      { id: "dev", groups: ["engineering"], projects: ["event-ingestion"], roles: [] },
    );

    expect(results).toHaveLength(1);
    expect(results[0]?.citation).toEqual({
      repository: "sample",
      path: "knowledge/architecture/idempotency.md",
      revision: "abc123",
    });
  });

  it("filters inaccessible projects and groups before scoring", async () => {
    const index = new InMemoryKnowledgeIndex(chunks);
    const results = await index.search(
      { query: "billing", project: "billing", limit: 8 },
      { id: "dev", groups: ["engineering"], projects: ["event-ingestion"], roles: [] },
    );

    expect(results).toEqual([]);
  });
});

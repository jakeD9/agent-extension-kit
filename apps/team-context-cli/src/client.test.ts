import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { StaticBearerAuthenticator } from "@agent-extension-kit/auth";
import { buildApp } from "../../context-service/src/app.js";
import { loadContentPack } from "../../context-service/src/content-pack.js";
import { ContextClient } from "./client.js";
import { createMcpHandlers } from "./mcp.js";

const running: Array<{ close(): Promise<unknown> }> = [];
afterEach(async () => Promise.all(running.splice(0).map((app) => app.close())));

async function fixtureClient() {
  const pack = await loadContentPack(resolve(import.meta.dirname, "../../../extension"), "fixture-revision");
  const app = buildApp({
    pack,
    authenticator: new StaticBearerAuthenticator(new Map([
      ["token", { id: "dev", groups: ["engineering"], projects: ["event-ingestion"], roles: [] }],
    ])),
    logger: false,
  });
  running.push(app);
  const url = await app.listen({ host: "127.0.0.1", port: 0 });
  return new ContextClient(url, "token");
}

describe("local adapters", () => {
  it("preserves REST search results through the CLI client", async () => {
    const client = await fixtureClient();
    const response = await client.searchKnowledge({ query: "vendor identifier", project: "event-ingestion", limit: 8 });

    expect(response.results[0]?.citation.revision).toBe("fixture-revision");
  });

  it("preserves REST search results through the MCP handler", async () => {
    const client = await fixtureClient();
    const handlers = createMcpHandlers(client);
    const response = await handlers.searchTeamKnowledge({ query: "vendor identifier", project: "event-ingestion", limit: 8 });

    expect(response.results[0]?.citation.revision).toBe("fixture-revision");
  });

  it("discovers an authorized skill through the shared service", async () => {
    const client = await fixtureClient();

    expect(await client.listSkills("event-ingestion")).toMatchObject({ items: [{ name: "diagnose-and-fix" }] });
    expect((await client.getSkill("diagnose-and-fix", "event-ingestion")).body).toContain("## Procedure");
  });
});

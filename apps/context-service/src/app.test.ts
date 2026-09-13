import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { StaticBearerAuthenticator } from "@agent-extension-kit/auth";
import { loadContentPack } from "./content-pack.js";
import { buildApp } from "./app.js";

const contentPath = resolve(import.meta.dirname, "../../../extension");

async function testApp() {
  const pack = await loadContentPack(contentPath, "fixture-revision");
  return buildApp({
    pack,
    authenticator: new StaticBearerAuthenticator(new Map([
      ["engineering-token", { id: "dev-1", groups: ["engineering"], projects: ["event-ingestion"], roles: [] }],
      ["outsider-token", { id: "dev-2", groups: ["other"], projects: ["other"], roles: [] }],
    ])),
    logger: false,
  });
}

describe("context service", () => {
  it("returns cited sample knowledge to an authorized caller", async () => {
    const app = await testApp();
    const response = await app.inject({
      method: "POST",
      url: "/v1/knowledge/search",
      headers: { authorization: "Bearer engineering-token" },
      payload: { query: "idempotency vendor identifier", project: "event-ingestion" },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().results[0].citation).toEqual({
      repository: "agent-extension-kit-sample",
      path: "knowledge/architecture/idempotency.md",
      revision: "fixture-revision",
      heading: "Stable event identity",
    });
    await app.close();
  });

  it("returns no content across an authorization boundary", async () => {
    const app = await testApp();
    const response = await app.inject({
      method: "POST",
      url: "/v1/knowledge/search",
      headers: { authorization: "Bearer outsider-token" },
      payload: { query: "idempotency", project: "event-ingestion" },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().results).toEqual([]);
    await app.close();
  });

  it("uses the canonical error envelope for invalid authentication", async () => {
    const app = await testApp();
    const response = await app.inject({ method: "POST", url: "/v1/knowledge/search", payload: {} });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toMatchObject({ error: { code: "unauthorized" } });
    expect(response.json().error.requestId).toBeTypeOf("string");
    await app.close();
  });
});

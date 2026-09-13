#!/usr/bin/env node
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { ContextClient } from "./client.js";
import { createMcpServer } from "./mcp.js";

const [command, subcommand, ...args] = process.argv.slice(2);
const client = new ContextClient(process.env.TEAM_CONTEXT_URL ?? "http://127.0.0.1:3000", process.env.TEAM_CONTEXT_TOKEN ?? "dev-token");
const option = (name: string, fallback?: string) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
};

if (command === "mcp") {
  void serveStdio(() => createMcpServer(client));
} else {
  let result: unknown;
  if (command === "search") {
    result = await client.searchKnowledge({ query: subcommand ?? "", project: option("--project", "event-ingestion")!, limit: Number(option("--limit", "8")) });
  } else if (command === "skill" && subcommand === "list") {
    result = await client.listSkills(option("--project", "event-ingestion")!);
  } else if (command === "skill" && subcommand === "get") {
    result = await client.getSkill(args[0] ?? "", option("--project", "event-ingestion")!);
  } else {
    process.stderr.write("Usage: team-context search <query> --project <id> | skill list --project <id> | skill get <name> --project <id> | mcp\n");
    process.exitCode = 2;
  }
  if (result !== undefined) process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

import { readFile, readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import matter from "gray-matter";
import { parse as parseYaml } from "yaml";
import { z } from "zod";
import type { KnowledgeChunk } from "@agent-extension-kit/context-core";

const ManifestSchema = z.object({
  schemaVersion: z.literal("1"),
  id: z.string().min(1),
  compatibility: z.string().optional(),
  displayName: z.string().min(1),
  kitCompatibility: z.string().min(1),
  projects: z.array(z.object({ id: z.string().min(1), accessGroups: z.array(z.string()) })),
  accessGroups: z.array(z.string()).optional(),
  defaultPolicy: z.string().optional(),
  defaultAuthorization: z.object({ policy: z.string().optional(), defaultGroups: z.array(z.string()).optional() }).optional(),
  content: z.object({ roots: z.array(z.string()) }).optional(),
  knowledge: z.object({ roots: z.array(z.string()) }),
  skills: z.object({ roots: z.array(z.string()) }).or(z.object({ root: z.string() })),
  metadata: z.record(z.string(), z.unknown()).optional(),
  repository: z.string().min(1),
}).passthrough();

export interface LoadedSkill {
  name: string;
  description: string;
  body: string;
  path: string;
  projects: string[];
  accessGroups: string[];
}

export interface ContentPack {
  manifest: z.infer<typeof ManifestSchema>;
  chunks: KnowledgeChunk[];
  skills: LoadedSkill[];
}

async function markdownFiles(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return markdownFiles(path);
    return entry.isFile() && entry.name.endsWith(".md") ? [path] : [];
  }));
  return nested.flat().sort();
}

const portablePath = (value: string) => value.split(sep).join("/");

export async function loadContentPack(root: string, revision: string): Promise<ContentPack> {
  const manifestText = await readFile(join(root, "manifest.yaml"), "utf8");
  const manifest = ManifestSchema.parse(parseYaml(manifestText));
  const chunks: KnowledgeChunk[] = [];

  for (const knowledgeRoot of manifest.knowledge.roots) {
    for (const path of await markdownFiles(join(root, knowledgeRoot))) {
      const parsed = matter(await readFile(path, "utf8"));
      const metadata = z.object({
        project: z.string(),
        accessGroups: z.array(z.string()),
        authority: z.enum(["approved", "proposed", "superseded"]),
        title: z.string(),
      }).parse(parsed.data);
      const relativePath = portablePath(relative(root, path));
      const heading = parsed.content.match(/^#\s+(.+)$/m)?.[1];
      chunks.push({
        id: `${manifest.id}:${relativePath}`,
        ...metadata,
        body: parsed.content.trim(),
        citation: {
          repository: manifest.repository,
          path: relativePath,
          revision,
          ...(heading ? { heading } : {}),
        },
      });
    }
  }

  const skillRoot = "root" in manifest.skills ? manifest.skills.root : manifest.skills.roots[0];
  const skills: LoadedSkill[] = [];
  if (skillRoot) {
    for (const path of await markdownFiles(join(root, skillRoot))) {
      if (!path.endsWith("SKILL.md")) continue;
      const parsed = matter(await readFile(path, "utf8"));
      const metadata = z.object({
        name: z.string(),
        description: z.string(),
        projects: z.array(z.string()).default([]),
        accessGroups: z.array(z.string()).default([]),
      }).passthrough().parse(parsed.data);
      skills.push({ ...metadata, body: parsed.content.trim(), path: portablePath(relative(root, path)) });
    }
  }

  return { manifest, chunks, skills };
}

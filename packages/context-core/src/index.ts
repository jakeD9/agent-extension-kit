import type { KnowledgeResult, KnowledgeSearchRequest, Principal } from "@agent-extension-kit/contracts";

export interface KnowledgeChunk {
  id: string;
  project: string;
  accessGroups: string[];
  authority: "approved" | "proposed" | "superseded";
  title: string;
  body: string;
  citation: {
    repository: string;
    path: string;
    revision: string;
    heading?: string;
  };
}

export interface KnowledgeIndex {
  search(request: KnowledgeSearchRequest, principal: Principal): Promise<KnowledgeResult[]>;
}

const tokenize = (value: string): string[] =>
  value
    .toLocaleLowerCase()
    .normalize("NFKD")
    .split(/[^\p{L}\p{N}_-]+/u)
    .filter((token) => token.length > 1);

export class InMemoryKnowledgeIndex implements KnowledgeIndex {
  constructor(private readonly chunks: readonly KnowledgeChunk[]) {}

  async search(request: KnowledgeSearchRequest, principal: Principal): Promise<KnowledgeResult[]> {
    if (!principal.projects.includes(request.project)) return [];
    const queryTerms = new Set(tokenize(request.query));

    return this.chunks
      .filter((chunk) => chunk.project === request.project)
      .filter((chunk) => chunk.accessGroups.some((group) => principal.groups.includes(group)))
      .filter((chunk) => chunk.authority === "approved")
      .map((chunk) => {
        const titleTerms = tokenize(chunk.title);
        const bodyTerms = tokenize(chunk.body);
        const score = titleTerms.reduce((sum, term) => sum + (queryTerms.has(term) ? 3 : 0), 0)
          + bodyTerms.reduce((sum, term) => sum + (queryTerms.has(term) ? 1 : 0), 0);
        return { chunk, score };
      })
      .filter(({ score }) => score > 0)
      .sort((left, right) => right.score - left.score || left.chunk.id.localeCompare(right.chunk.id))
      .slice(0, request.limit)
      .map(({ chunk, score }) => ({
        id: chunk.id,
        title: chunk.title,
        excerpt: chunk.body.slice(0, 500),
        score,
        authority: chunk.authority,
        citation: chunk.citation,
      }));
  }
}

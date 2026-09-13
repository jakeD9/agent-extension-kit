# syntax=docker/dockerfile:1.7
FROM node:24-alpine AS workspace
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json vitest.config.ts ./
COPY apps ./apps
COPY packages ./packages
COPY extension ./extension
RUN pnpm install --frozen-lockfile
RUN pnpm build

FROM workspace AS team-context
ENV NODE_ENV=production HTTP_PORT=3000 CONTENT_PATH=/app/extension
USER node
EXPOSE 3000
CMD ["pnpm", "--filter", "@agent-extension-kit/context-service", "start"]

FROM workspace AS slack-agent
ENV NODE_ENV=production HTTP_PORT=3001 PROCESS_MODE=serve-slack
USER node
EXPOSE 3001
CMD ["pnpm", "--filter", "@agent-extension-kit/slack-agent", "start"]

FROM workspace AS coding-runner
ENV NODE_ENV=production
USER node
CMD ["pnpm", "--filter", "@agent-extension-kit/coding-runner", "start"]

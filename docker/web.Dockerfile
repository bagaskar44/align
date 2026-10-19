FROM node:22-alpine AS dependencies
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN corepack pnpm install --frozen-lockfile

FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY --from=dependencies /app/node_modules /app/node_modules
COPY --from=dependencies /app/apps/web/node_modules /app/apps/web/node_modules
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web apps/web
RUN corepack pnpm --dir apps/web build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build /app/apps/web/.next/standalone ./
COPY --from=build /app/apps/web/.next/static ./apps/web/.next/static
EXPOSE 3000
USER node
CMD ["node", "apps/web/server.js"]


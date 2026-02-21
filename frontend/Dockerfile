###############################################################
# FILE: frontend/Dockerfile
# Role: Vue 3 + Vite frontend container (dev and prod stages).
# Agent boundary: Deployment layer
# Dependencies: package.json; node_modules installed
# How to test (dev):  docker compose up frontend → http://localhost:5173
# How to test (prod): docker compose build frontend (target=prod)
###############################################################

# ── Dev stage ─────────────────────────────────────────────────
FROM node:22-alpine AS dev

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ── Build stage ────────────────────────────────────────────────
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

# ── Prod stage (nginx static serve) ───────────────────────────
FROM nginx:alpine AS prod

COPY --from=build /app/dist /usr/share/nginx/html
# Override default nginx config for Vue Router history mode
COPY nginx-spa.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

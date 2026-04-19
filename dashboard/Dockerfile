# Multi-stage build: node compiles the Vite bundle, nginx serves it.
# Nginx proxies /api and /ws to the FastAPI `api` service over the compose network.

FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

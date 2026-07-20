# Frontend image: build the static Vite bundle, then serve it with nginx.
# NOT built/tested in this development environment (no Docker CLI available
# there) — standard two-stage pattern, but treat the first real build as a
# verification step. See docs/deployment.md.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Bake the backend URL in at build time; override with --build-arg for a non-default deployment.
ARG VITE_API_BASE_URL=http://localhost:8123
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

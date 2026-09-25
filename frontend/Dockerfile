FROM node:25-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Rendered from the template with envsubst by the official nginx entrypoint
# (docker-entrypoint.d/20-envsubst-on-templates.sh) on container start, using
# only variables present in the container's environment — this is how
# API_AUTH_TOKEN is injected into the reverse-proxy config at runtime.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80

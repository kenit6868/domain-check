FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_FORM_ASSISTANT_EXTENSION_ID
ENV VITE_FORM_ASSISTANT_EXTENSION_ID=$VITE_FORM_ASSISTANT_EXTENSION_ID
RUN npm run build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv

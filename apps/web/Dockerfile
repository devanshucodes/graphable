FROM node:24.21.0-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build
EXPOSE 3001
CMD ["npm", "run", "start"]

# ---- Stage 1: Build ----
FROM node:20-alpine AS builder
WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm ci

# Copy source and build
# Override VITE_API_BASE_URL to empty so the app uses relative /api/ paths
# that nginx will proxy to the backend service
COPY . .
RUN VITE_API_BASE_URL="" npm run build

# ---- Stage 2: Serve ----
FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

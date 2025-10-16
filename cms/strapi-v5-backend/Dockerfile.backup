# ==================================
# GLAD LABS - Strapi CMS Dockerfile
# ==================================
# Multi-stage build for optimized production image

# Stage 1: Dependencies
FROM node:20-alpine AS dependencies

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci --only=production && \
    npm cache clean --force

# Stage 2: Builder
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install all dependencies (including devDependencies)
RUN npm ci

# Copy source code
COPY . .

# Set environment for build
ENV NODE_ENV=production

# Build admin panel
RUN npm run build

# Stage 3: Production
FROM node:20-alpine AS production

WORKDIR /app

# Install dumb-init for proper signal handling
RUN apk add --no-cache dumb-init

# Create non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S strapi -u 1001

# Copy production dependencies
COPY --from=dependencies --chown=strapi:nodejs /app/node_modules ./node_modules

# Copy built application
COPY --from=builder --chown=strapi:nodejs /app/dist ./dist
COPY --from=builder --chown=strapi:nodejs /app/build ./build
COPY --from=builder --chown=strapi:nodejs /app/public ./public
COPY --from=builder --chown=strapi:nodejs /app/package.json ./

# Copy config and database (if using SQLite)
COPY --chown=strapi:nodejs config ./config
COPY --chown=strapi:nodejs database ./database
COPY --chown=strapi:nodejs src ./src

# Create necessary directories
RUN mkdir -p .tmp/data && chown -R strapi:nodejs .tmp

# Switch to non-root user
USER strapi

# Expose Strapi port
EXPOSE 1337

# Set environment
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=1337

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD node -e "require('http').get('http://localhost:1337/_health', (r) => {process.exit(r.statusCode === 200 ? 0 : 1)})"

# Start application with dumb-init
ENTRYPOINT ["dumb-init", "--"]
CMD ["npm", "run", "start"]

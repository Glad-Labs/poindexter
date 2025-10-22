#!/bin/bash
set -e

echo "Installing dependencies with yarn..."
# Remove --frozen-lockfile to allow yarn to update lockfile if needed
yarn install --non-interactive

echo "Building Strapi..."
yarn run build

echo "Build complete!"

#!/bin/bash
set -e

echo "Installing dependencies with yarn..."
yarn install

echo "Building Strapi..."
yarn run build

echo "Build complete!"

#!/bin/bash
# Start Strapi CMS with proper node module resolution

# Set NODE_PATH to include both local and root node_modules
export NODE_PATH="./node_modules:../node_modules:../../node_modules:$NODE_PATH"

# Run Strapi develop
npm run develop

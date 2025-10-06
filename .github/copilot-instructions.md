# Glad Labs Website - AI Coding Instructions

This document provides essential context for working within the Glad Labs monorepo.

## Architecture Overview

This is a `npm` workspace-based monorepo containing three primary web services and a Python agent.

1.  **`cms/strapi-backend`**: A **Strapi v4** headless CMS. This is the central content repository and the source of truth for the public website.
2.  **`web/public-site`**: A **Next.js** application that serves the public-facing website. It fetches all its content from the Strapi API.
3.  **`web/oversight-hub`**: A **Create React App** dashboard that integrates with **Firebase** for its data and authentication. See `web/oversight-hub/src/firebaseConfig.js`.
4.  **`agents/content-agent`**: A Python-based agent for content-related tasks.

## Critical Developer Workflows

### Running the Full Stack

The most important workflow is running all services simultaneously. Always use the root-level command:

```bash
npm run start:all
```

This uses `concurrently` to launch all three web services on their designated ports:

- **Strapi Backend**: `http://localhost:1337`
- **Oversight Hub**: `http://localhost:3001`
- **Public Site**: `http://localhost:3002`

### Node.js Version Requirement

This project **requires a Node.js version between 18.x and 20.x** due to dependencies in Strapi v4. Using a version manager like `nvm` is highly recommended.

### Monorepo Commands

To run commands for a specific workspace, use the `--workspace` flag from the root directory. For example, to install a dependency only in the public site:

```bash
npm install <package-name> --workspace=glad-labs-public-site
```

## Project-Specific Conventions

### Strapi Schema and Extensions

- Strapi content type schemas are defined in `cms/strapi-backend/src/api/{content-type}/content-types/{content-type}/schema.json`.
- **Crucially, to add a relationship to a built-in Strapi model (like `User`), you must formally extend the plugin.** For example, to add a `posts` relation to the `User` model, a schema extension was created at: `cms/strapi-backend/src/extensions/users-permissions/content-types/user/schema.json`. Directly modifying the `post` schema's `author` relation without this extension will cause the server to fail.

### Generated Types

- Strapi auto-generates TypeScript definitions in `cms/strapi-backend/types/generated/`.
- These files (`contentTypes.d.ts`, `components.d.ts`) **should be checked into Git** to ensure type safety and consistency between the backend schema and frontend consumers.

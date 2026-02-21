# MCP Servers Reference Guide

**Last Updated:** February 20, 2026  
**Status:** Production-ready configuration with optional enhancements

## Server Status Matrix

| Server | Status | Type | Use Case | Enabled |
|--------|--------|------|----------|---------|
| **Filesystem** | ✅ Active | Core | File operations | YES |
| **Git** | ✅ Active | Core | Version control | YES |
| **Memory** | ✅ Active | Core | Knowledge graph | YES |
| **Sequential Thinking** | ✅ Active | Enhanced | Complex reasoning | YES |
| **Time** | ✅ Active | Enhanced | Timezone utilities | YES |
| **PostgreSQL** | ✅ Available | Optional | Database queries | NO (commented) |
| **Playwright** | ✅ Available | Optional | Browser testing | NO (commented) |
| **Docker** | ✅ Available | Optional | Container mgmt | NO (commented) |
| **Bash** | ✅ Available | Optional | Shell isolation | NO (commented) |
| **Fetch** | ❌ Archived | N/A | Web content | NOT USED |

---

## Core Servers (ALWAYS ENABLED)

### 1. Filesystem

**Purpose:** Secure file operations  
**Capability:** Read, write, create, delete files within workspace  
**Command:** `npx @modelcontextprotocol/server-filesystem <workspace-path>`

**When the agent uses it:**

- Reading Python files to understand structure
- Creating new components or modules
- Updating configuration files
- Exploring project layout

**Example workflow:**

```
User: "Add error handling to the WritingStyleIntegrationService"
  → Agent uses Filesystem to:
    1. Read current WritingStyleIntegrationService.py
    2. Understand error handling patterns
    3. Create improved version with try/catch blocks
```

---

### 2. Git

**Purpose:** Version control operations  
**Capability:** Read commit history, show diffs, manage branches  
**Command:** `uvx mcp-server-git --repository <workspace-path>`

**When the agent uses it:**

- Checking recent commits to understand changes
- Creating feature branches for safe development
- Reviewing what changed between versions
- Staging and committing code

**Example workflow:**

```
User: "Fix the WritingStyleIntegrationService bug on a feature branch"
  → Agent uses Git to:
    1. Create feature/writing-style-fix branch
    2. Implement fix
    3. Show diff of changes
    4. Commit with descriptive message
```

---

### 3. Memory

**Purpose:** Persistent knowledge graph across sessions  
**Capability:** Store facts, patterns, and codebase understanding  
**Command:** `npx @modelcontextprotocol/server-memory`

**When the agent uses it:**

- Recording Glad Labs architecture patterns
- Storing "discovered facts" about conventions
- Remembering user preferences for coding style
- Building project-specific knowledge

**Example facts stored:**

```
Fact: "Glad Labs uses model_router.py for cost-optimized LLM selection"
Fact: "Python type hints required in all backend code"
Fact: "React components use functional patterns with hooks"
Fact: "Database operations happen via DatabaseService, not direct SQL"
```

---

### 4. Sequential Thinking (NEW - Uncommented)

**Purpose:** Reflective, multi-step problem-solving  
**Capability:** Structured reasoning through complex tasks  
**Command:** `npx @modelcontextprotocol/server-sequentialthinking`

**When the agent uses it:**

- Decomposing multi-file refactoring tasks
- Planning workflow execution strategies
- Debugging complex integration issues
- Designing new features with dependencies

**Example workflow:**

```
User: "Integrate capability system into task creation"
  → Agent uses Sequential Thinking to:
    1. Define problem: "How do capabilities compose into tasks?"
    2. Identify dependencies: "Need registry + routing logic"
    3. Plan phases: "Registry → Router → Integration → Testing"
    4. Execute each phase with full context
    5. Validate against business requirements
```

---

### 5. Time (NEW - Enhanced)

**Purpose:** Timezone and date utilities  
**Capability:** Convert timezones, format dates, manage scheduling  
**Command:** `npx @modelcontextprotocol/server-time`

**When the agent uses it:**

- Checking deployment windows (understanding scheduling)
- Calculating timezone offsets for international teams
- Timestamping important events in logs
- Understanding deadline contexts

---

## Optional Servers (Currently Disabled - Uncomment to Enable)

### PostgreSQL - Database Access

```yaml
- name: postgres
  command: npx
  args:
    - "@modelcontextprotocol/server-postgres"
    - "postgresql://user:password@localhost:5432/glad_labs"
  description: "PostgreSQL database querying and schema inspection"
```

**When to enable it:**

- You want the agent to query development database directly
- Testing data transformations or migrations
- Debugging data-related bugs
- Analyzing schema relationships

**Security note:** Only use with development database, never production credentials.

**Example use:**

```
User: "Query the writing_samples table to understand structure"
  → Agent uses PostgreSQL to:
    1. SHOW SCHEMA writing_samples
    2. SELECT * FROM writing_samples LIMIT 5
    3. Explain data relationships to you
```

---

### Playwright - Browser Automation

```yaml
- name: playwright
  command: npx
  args:
    - "@microsoft/playwright-mcp"
    - "--headless"
  description: "Browser automation for testing and web scraping"
```

**When to enable it:**

- Testing UI flows automatically
- Capturing screenshots for documentation
- Scraping web data for analysis
- Testing OAuth/auth flows
- Validating frontend integration

**Prerequisites:**

```bash
npm install -g @playwright/test
# Or globally: npm install -g playwright
```

**Example use:**

```
User: "Test the task creation form with various inputs"
  → Agent uses Playwright to:
    1. Navigate to http://localhost:3001
    2. Fill form with test data
    3. Submit and capture response
    4. Verify success message appears
    5. Screenshot workflow results
```

---

### Docker - Container Management

```yaml
- name: docker
  command: npx
  args:
    - "@modelcontextprotocol/server-docker"
  description: "Docker container management and orchestration"
```

**When to enable it:**

- Spinning up isolated test environments
- Managing PostgreSQL/Redis containers
- Building and running container images
- Testing containerized deployment

**Prerequisites:** Docker Desktop running

**Example use:**

```
User: "Start a PostgreSQL container for integration testing"
  → Agent uses Docker to:
    1. docker pull postgres:15
    2. docker run -e POSTGRES_PASSWORD=test -p 5432:5432 postgres:15
    3. Wait for healthcheck
    4. Run migrations
    5. Execute tests
    6. Cleanup
```

---

### Bash - Shell Command Isolation

```yaml
- name: bash
  command: npx
  args:
    - "@modelcontextprotocol/server-bash"
    - "c:\\Users\\mattm\\glad-labs-website"
  description: "Secure bash command execution in safe environment"
```

**When to enable it:**

- Running npm/python commands in isolated fashion
- Complex multi-step shell operations
- When you want execution logs and safety
- Testing CI/CD pipelines locally

**Different from Continue's built-in shell:**

- Continues built-in shell: Direct, immediate
- Bash MCP server: Sandboxed, logged, confirmable

**Example use:**

```
User: "Run the full test suite and show results"
  → Agent uses Bash to:
    1. npm run test:python
    2. npm run test:frontend
    3. Collect all output
    4. Parse results
    5. Generate summary report
```

---

## Why These Servers Were Chosen

### For Glad Labs Specifically

1. **Filesystem** - Essential for Python + Node.js monorepo work
2. **Git** - Feature branch development is critical
3. **Memory** - Glad Labs has complex architecture patterns worth remembering
4. **Sequential Thinking** - Workflow execution needs structured planning
5. **Time** - Multi-service coordination needs timezone awareness
6. **PostgreSQL** (optional) - Direct backend database access for testing
7. **Playwright** (optional) - Testing React/Next.js frontends
8. **Docker** (optional) - Container-based service isolation
9. **Bash** (optional) - Robust npm/poetry command execution

### Servers NOT Included

- **Fetch** - Failed on npm registry (not published)
- **GitHub** - Archived in official repo; use Git server instead
- **Ollama** - Continue already handles local model execution
- **Search engines** - Not critical for autonomous local coding

---

## Quick Enable/Disable Guide

### To Enable a Server

1. Open `c:\Users\mattm\.continue\config.yaml`
2. Find the commented server (lines starting with `#`)
3. Remove `#` from the beginning of each line in that server block
4. Customize any paths or credentials
5. Restart VS Code completely
6. Check Continue panel for "✓ Loaded MCP Server: <name>"

### To Disable a Server

1. Add `#` to the beginning of each line in the server block
2. Restart VS Code

### To Modify Server Arguments

Update the `args:` section with new parameters, then restart VS Code.

---

## Troubleshooting Servers

### Server fails to load?

1. Check the command exists: `npx --version` or `uvx --version`
2. Verify paths are correct (workspace, database URL, etc.)
3. Check firewall/port conflicts (especially PostgreSQL on 5432)
4. Look at VS Code output panel (View → Output → Continue) for error messages

### Server loads but not responding?

1. Check if it requires credentials (PostgreSQL, etc.)
2. Ensure external services are running (PostgreSQL, Docker daemon)
3. Restart the server: Close VS Code, reopen

### Performance issues with file operations?

1. Reduce `maxLength` in Filesystem args
2. Disable repo-map context provider if not needed
3. Use narrower workspace paths for Filesystem

---

## Production Readiness

**Current Configuration Status:**

- ✅ **Development-ready**: 5 active servers (Filesystem, Git, Memory, Sequential Thinking, Time)
- ✅ **Extensible**: 4 optional servers ready to enable
- ⚠️ **Not production**: Some servers need credentials (PostgreSQL, Docker)

**Before using in production:**

1. Add authentication to optional servers
2. Use read-only database connections for PostgreSQL
3. Test Docker container isolation
4. Review Continue's autonomy rules for security

---

## See Also

- [Continue Config Docs](https://docs.continue.dev/)
- [MCP Registry](https://registry.modelcontextprotocol.io/)
- [MCP GitHub Servers](https://github.com/modelcontextprotocol/servers)
- Main MCP Setup Guide: [MCP_SERVERS_SETUP.md](./MCP_SERVERS_SETUP.md)

# Real MCP Servers Setup Guide

**For Fully Autonomous Local LLM Coding Agent**

Last Updated: February 20, 2026

---

## Quick Start

### Step 1: Install Required Tools

```bash
# Install Node.js (if not already installed)
# Download from https://nodejs.org/ (v18+)

# Install Python uv (recommended for Python servers)
pip install uv

# OR use npm/npx (comes with Node.js)
npm install -g npx
```

### Step 2: Update Your Paths

Edit your Continue config (`c:\Users\mattm\.continue\config.yaml`) and replace:

```yaml
/path/to/workspace  # Replace with your actual workspace path
```

For Glad Labs:

```yaml
- name: filesystem
  command: npx
  args:
    - "@modelcontextprotocol/server-filesystem"
    - "c:\\Users\\mattm\\glad-labs-website"  # Your workspace root

- name: git
  command: uvx
  args:
    - "mcp-server-git"
    - "--repository"
    - "c:\\Users\\mattm\\glad-labs-website"  # Your workspace root
```

### Step 3: Test MCP Servers

```bash
# Test each server individually to ensure they work:

# Filesystem server
npx @modelcontextprotocol/server-filesystem "c:\Users\mattm\glad-labs-website"

# Git server
uvx mcp-server-git --repository "c:\Users\mattm\glad-labs-website"

# Fetch server
npx @modelcontextprotocol/server-fetch

# Memory server
npx @modelcontextprotocol/server-memory
```

If all show output without errors, setup is complete!

---

## Official MCP Servers (Recommended for Autonomous Coding)

### Core Servers (Essential)

#### 1. **Filesystem Server**

📦 `@modelcontextprotocol/server-filesystem`

- **What it does:** Secure file read/write/create operations
- **Installation:** `npx @modelcontextprotocol/server-filesystem /path`
- **Tools provided:**
  - `read_file` - Read file contents
  - `write_file` - Create/overwrite files
  - `list_directory` - Browse directories
  - `search_files` - Search by filename/pattern
- **Autonomy benefit:** Your agent can directly manipulate code files

#### 2. **Git Server**

📦 `mcp-server-git` (via uvx)

- **What it does:** Read, search, and manipulate Git repositories
- **Installation:** `uvx mcp-server-git --repository /path`
- **Tools provided:**
  - `read_repository_file` - Read versioned files
  - `get_commit_history` - View git history
  - `branch_info` - List branches
  - `get_diff` - View changes
- **Autonomy benefit:** Your agent can commit code, create branches, track changes

#### 3. **Fetch Server**

📦 `@modelcontextprotocol/server-fetch`

- **What it does:** Download and convert web content to markdown
- **Installation:** `npx @modelcontextprotocol/server-fetch`
- **Tools provided:**
  - `fetch_url` - Download web content
  - `convert_to_markdown` - Convert HTML/PDF to markdown
- **Autonomy benefit:** Your agent can research documentation, APIs, examples

#### 4. **Memory Server**

📦 `@modelcontextprotocol/server-memory`

- **What it does:** Persistent knowledge graph across sessions
- **Installation:** `npx @modelcontextprotocol/server-memory`
- **Tools provided:**
  - `create_knowledge` - Save facts/context
  - `search_memory` - Query stored knowledge
  - `list_memory` - Browse stored items
- **Autonomy benefit:** Your agent remembers project patterns, decisions, architecture

### Recommended Optional Servers

#### 5. **Sequential Thinking Server**

📦 `@modelcontextprotocol/server-sequentialthinking`

- **What it does:** Structured problem-solving with thought sequences
- **Installation:** `npx @modelcontextprotocol/server-sequentialthinking`
- **Autonomy benefit:** Deepseek-R1 can show reasoning steps explicitly

#### 6. **Everything Server** (Reference/Test)

📦 `@modelcontextprotocol/server-everything`

- **What it does:** Multi-purpose server with prompts, resources, and tools
- **Installation:** `npx @modelcontextprotocol/server-everything`
- **Autonomy benefit:** Good for testing MCP integration

---

## Recommended MCP Servers for Your Monorepo

Since you're working on Glad Labs (Python + Next.js + React):

### Backend-Focused

```yaml
mcpServers:
  - name: filesystem
    command: npx
    args: ["@modelcontextprotocol/server-filesystem", "c:\\Users\\mattm\\glad-labs-website\\src"]
  
  - name: git
    command: uvx
    args: ["mcp-server-git", "--repository", "c:\\Users\\mattm\\glad-labs-website"]
  
  - name: fetch
    command: npx
    args: ["@modelcontextprotocol/server-fetch"]
```

### Full Monorepo Awareness

```yaml
mcpServers:
  # Root-level git access
  - name: git-root
    command: uvx
    args: ["mcp-server-git", "--repository", "c:\\Users\\mattm\\glad-labs-website"]
  
  # Backend files
  - name: filesystem-backend
    command: npx
    args: ["@modelcontextprotocol/server-filesystem", "c:\\Users\\mattm\\glad-labs-website\\src"]
  
  # Frontend files
  - name: filesystem-frontend
    command: npx
    args: ["@modelcontextprotocol/server-filesystem", "c:\\Users\\mattm\\glad-labs-website\\web"]
  
  # Web research
  - name: fetch
    command: npx
    args: ["@modelcontextprotocol/server-fetch"]
  
  # Session memory
  - name: memory
    command: npx
    args: ["@modelcontextprotocol/server-memory"]
```

---

## Workflow: Using MCP Servers with Your Agent

### Example 1: Implement Feature with Autonomous Coding

```
You (to Continue):
"Refactor the WritingStyleIntegrationService to add LLM-based analysis.
Review the current implementation, identify injection points,
implement the feature, test it, and commit changes."

Continue will:
1. Use Filesystem server → Read current service code
2. Use Fetch server → Look up relevant documentation
3. Use Memory server → Recall architectural patterns from project
4. Use Git server → Check branch status, create feature branch
5. Generate code → Implement feature
6. Test implementation → Run tests using shell commands
7. Git server → Commit with proper message
```

### Example 2: Debug and Fix Issues

```
You (to Continue):
"The workflow executor is timing out on complex workflows.
Find the bottleneck, optimize, test, and report."

Continue will:
1. Use Filesystem server → Read workflow executor code
2. Use Git server → Check recent changes that might have caused issue
3. Use Memory server → Reference past performance optimizations
4. Identify issue → Profile the slow code path
5. Optimize → Make improvements
6. Test → Run full test suite
7. Git server → Commit optimization with benchmark results
```

---

## Troubleshooting

### Server Not Starting

**Error:** `Command not found: npx`

```bash
# Solution: Install Node.js from https://nodejs.org/
# Verify:
node --version
npm --version
```

**Error:** `uvx: command not found`

```bash
# Solution: Install uv
pip install uv
# Or use npx instead for Python servers:
npx mcp-server-git  # This might not work, better to install uv
```

### Filesystem Permissions

If you get "Access Denied" errors:

1. Check that paths use forward slashes or escaped backslashes:

   ```yaml
   # ❌ Wrong
   - "C:\Users\mattm\path"
   
   # ✅ Correct
   - "C:\\Users\\mattm\\path"  # or
   - "C:/Users/mattm/path"
   ```

2. Ensure paths exist and user has read/write permissions

### Git Server Authentication

If git operations require authentication:

```bash
# Ensure git credentials are configured
git config --global user.name "Your Name"
git config --global user.email "your@email.com"

# For GitHub, use personal access token:
git config --global credential.helper wincred  # Windows
```

---

## Advanced: Building Custom MCP Servers

If you need functionality beyond official servers:

1. **JavaScript/TypeScript:**

   ```bash
   npm create @modelcontextprotocol/mcp-server@latest my-server
   cd my-server
   npm install
   npm run build
   ```

2. **Python:**

   ```bash
   pip install mcp
   # Create server at my_server.py
   # Run: python my_server.py
   ```

3. **Reference:** <https://github.com/modelcontextprotocol/servers>

---

## Next Steps

1. ✅ Update config.yaml with your workspace path
2. ✅ Test each MCP server with the test commands above
3. ✅ Restart Continue extension (VS Code: Reload Window)
4. ✅ Ask Continue to perform autonomous coding tasks
5. ✅ Monitor what tools Continue uses (Check Continue output panel)

---

## Resources

- **Official MCP Registry:** <https://registry.modelcontextprotocol.io/>
- **Official Servers:** <https://github.com/modelcontextprotocol/servers>
- **Community Servers:** 100+ more at registry (GitHub, Discord, AWS, etc.)
- **Documentation:** <https://modelcontextprotocol.io/>

---

## Your Configuration Summary

**Core Setup:** Filesystem + Git + Fetch + Memory
**Workspace:** `c:\Users\mattm\glad-labs-website`
**Local Models:** Deepseek-R1 (reasoning) + Qwen2.5-Coder (code generation)
**Autonomy Level:** Full - agent can read, write, test, commit code
**Cost:** Zero (all local, no API calls needed)

You're now ready for fully autonomous local LLM coding! 🚀

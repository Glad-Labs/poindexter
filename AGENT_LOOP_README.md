# Agent Loop - Autonomous Code Improvement

An autonomous agent system that uses local LLMs (via Ollama) to analyze your codebase, identify improvements, and apply fixes automatically.

## Features

- 🤖 **Autonomous Operation**: Plans and executes improvements without manual intervention
- 🧠 **Dual-Model Architecture**:
  - Reasoning model for planning and analysis
  - Specialized code model for generating patches
- 🔄 **Iterative Improvement**: Runs multiple iterations, testing after each change
- 🌐 **Fully Local**: Uses Ollama models - no external API calls or costs
- 🔒 **Safe**: Applies patches via `git apply` (can be reviewed/reverted)

## Prerequisites

1. **Ollama** - Download from <https://ollama.ai>
2. **Python 3.10+** with `requests` library
3. **Git** - For applying patches

## Quick Start

### 1. Install Ollama Models

**Windows:**

```bash
setup_agent_loop.bat
```

**Linux/Mac:**

```bash
bash setup_agent_loop.sh
```

This will install:

- `qwen2.5:14b` - Reasoning model (~8GB)
- `qwen2.5-coder:7b` - Code generation model (~4GB)

### 2. Start Ollama

```bash
ollama serve
```

Keep this running in a separate terminal.

### 3. Run the Agent Loop

```bash
python agent_loop.py
```

## How It Works

```
┌─────────────────────────────────────────┐
│  1. Run Tests                           │
│     └─> Collect test results           │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│  2. Reasoning Phase                     │
│     └─> Analyze codebase               │
│     └─> Identify improvements          │
│     └─> Create step-by-step plan       │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│  3. Code Generation                     │
│     └─> For each step:                 │
│         • Load relevant files          │
│         • Generate unified diff        │
│         • Apply patch                  │
│         • Run tests                    │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│  4. Validation                          │
│     └─> Tests pass? Continue           │
│     └─> Tests fail? Revert & learn     │
└─────────────────────────────────────────┘
```

## Configuration

Edit `agent_loop.py` to customize:

```python
# Models (adjust based on your hardware)
REASONER_MODEL = "qwen2.5:14b"      # Can use :7b for less RAM
CODER_MODEL = "qwen2.5-coder:7b"    # Can use :32b for better quality

# Limits
MAX_ITERATIONS = 5                   # Max improvement cycles
```

### Alternative Models

**For Less RAM (8GB):**

```python
REASONER_MODEL = "qwen2.5:7b"
CODER_MODEL = "qwen2.5-coder:3b"
```

**For Better Quality (32GB+):**

```python
REASONER_MODEL = "qwen2.5:32b"
CODER_MODEL = "deepseek-coder:33b"
```

**For Specialized Tasks:**

```python
CODER_MODEL = "codellama:34b"       # Meta's code specialist
CODER_MODEL = "starcoder2:15b"      # Alternative code model
```

## What It Improves

The agent looks for:

- 🐛 **Bugs** - Logic errors, edge cases
- 🏗️ **Structure** - Code organization, modularity
- 🧪 **Tests** - Missing tests, test coverage
- 📚 **Documentation** - Missing docstrings, outdated READMEs
- ⚡ **Performance** - Obvious inefficiencies
- 🎨 **Code Quality** - Linting issues, best practices

## Safety

- All changes are applied via `git apply` - you can review them in Git
- Tests run after each change to catch regressions
- Failed patches are logged but don't stop the loop
- You can revert any change with `git reset --hard HEAD~1`

## Troubleshooting

### "Ollama not available"

```bash
# Start Ollama server
ollama serve
```

### "Missing required models"

```bash
# Install models manually
ollama pull qwen2.5:14b
ollama pull qwen2.5-coder:7b
```

### "Command returned non-zero exit status"

- Your models might not be installed
- Ollama might not be running
- Model names might be incorrect (check with `ollama list`)

### Patch fails to apply

- The agent's diff might be malformed
- Files might have changed since analysis
- Usually safe to continue - next iteration will adapt

## Performance

**Typical iteration:**

- Analysis: 30-60 seconds
- Code generation: 15-30 seconds per file
- Total: 2-5 minutes per iteration

**Resource usage:**

- RAM: 8-16GB (depends on model size)
- CPU: Will use available cores
- GPU: Optional but speeds up inference 5-10x

## Advanced Usage

### Custom Test Command

Edit `run_tests()` function:

```python
def run_tests():
    proc = subprocess.run(
        ["npm", "test"],  # or ["python", "-m", "pytest"], etc.
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    return proc.returncode == 0, proc.stdout + "\n" + proc.stderr
```

### Exclude Files

Edit `list_repo_files()`:

```python
def list_repo_files():
    files = []
    for p in REPO_ROOT.rglob("*"):
        if ".git" in p.parts or "node_modules" in p.parts:
            continue
        # Add more exclusions here
        files.append(str(p.relative_to(REPO_ROOT)))
    return files
```

## Credits

Built with:

- [Ollama](https://ollama.ai) - Local LLM runtime
- [Qwen 2.5](https://qwenlm.github.io/) - Alibaba's reasoning models
- [Qwen 2.5 Coder](https://qwenlm.github.io/) - Specialized code model

## License

Same as parent project (see main LICENSE file)

import os
import pathlib
import sys

# Ensure both project root and src/ are on sys.path so `agents.*` modules import during tests
TEST_FILE = pathlib.Path(__file__).resolve()
# parents: [tests(0), content_agent(1), agents(2), src(3), repo_root(4)]
REPO_ROOT = TEST_FILE.parents[4]
SRC = REPO_ROOT / "src"
CONTENT_AGENT = SRC / "agents" / "content_agent"
for p in (str(REPO_ROOT), str(SRC), str(CONTENT_AGENT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Make tests default to non-strict config validation and prevent dotenv loading
os.environ.setdefault("STRICT_ENV_VALIDATION", "0")
os.environ.setdefault("DISABLE_DOTENV", "1")

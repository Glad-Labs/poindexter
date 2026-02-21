@echo off
REM Setup script for agent_loop.py on Windows
REM This will install required Ollama models

echo 🔧 Setting up Agent Loop...

REM Check if Ollama is installed
where ollama >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Ollama is not installed. Please install it first:
    echo    https://ollama.ai
    exit /b 1
)

REM Check if Ollama is running
curl -s http://localhost:11434/api/tags >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ⚠️  Ollama is not running. Please start it first:
    echo    Open a new terminal and run: ollama serve
    echo.
    echo    Then run this script again.
    exit /b 1
)

echo ✅ Ollama is running
echo.
echo 📦 Installing recommended models...
echo    This may take 10-20 minutes depending on your connection
echo.

REM Reasoning model (smaller, more reliable)
echo 1️⃣  Installing qwen2.5:14b (reasoning model)...
ollama pull qwen2.5:14b

REM Coding model
echo 2️⃣  Installing qwen2.5-coder:7b (code generation)...
ollama pull qwen2.5-coder:7b

echo.
echo ✅ Setup complete! You can now run:
echo    python agent_loop.py
echo.
echo Alternative models (if you have more RAM/GPU):
echo    - qwen2.5:32b (better reasoning)
echo    - qwen2.5-coder:32b (better code generation)
echo    - deepseek-coder:33b (excellent for code)
echo.
pause

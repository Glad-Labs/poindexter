#!/bin/bash
# Setup script for agent_loop.py
# This will install required Ollama models

echo "🔧 Setting up Agent Loop..."

# Check if Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "❌ Ollama is not installed. Please install it first:"
    echo "   https://ollama.ai"
    exit 1
fi

# Check if Ollama is running
if ! curl -s http://localhost:11434/api/tags &> /dev/null; then
    echo "⚠️  Ollama is not running. Starting it..."
    echo "   Run: ollama serve"
    echo ""
    echo "   Then run this script again."
    exit 1
fi

echo "✅ Ollama is running"

# Install recommended models
echo ""
echo "📦 Installing recommended models..."
echo "   This may take 10-20 minutes depending on your connection"
echo ""

# Reasoning model (smaller, more reliable than deepseek-r1)
echo "1️⃣  Installing qwen2.5:14b (reasoning model)..."
ollama pull qwen2.5:14b

# Coding model
echo "2️⃣  Installing qwen2.5-coder:7b (code generation)..."
ollama pull qwen2.5-coder:7b

echo ""
echo "✅ Setup complete! You can now run:"
echo "   python agent_loop.py"
echo ""
echo "Alternative models (if you have more RAM/GPU):"
echo "   - qwen2.5:32b (better reasoning)"
echo "   - qwen2.5-coder:32b (better code generation)"
echo "   - deepseek-coder:33b (excellent for code)"

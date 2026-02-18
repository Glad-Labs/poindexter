#!/bin/bash
# OVERSIGHTHUB_WORKFLOW_API_QUICK_REFERENCE.sh
# Quick reference for testing workflows via API without authentication hassles

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

function check_backend_health() {
  echo "🔍 Checking backend health..."
  curl -s http://localhost:8000/health | python3 -m json.tool
}

function list_workflow_templates() {
  echo "📋 Available Workflow Templates:"
  curl -s -X POST http://localhost:8000/api/workflows/templates \
    -H "Content-Type: application/json" \
    -d '{}' | python3 -m json.tool
}

function get_workflow_history() {
  echo "📜 Workflow Execution History (last 10):"
  curl -s http://localhost:8000/api/workflows/history?limit=10 2>/dev/null || echo "⚠️  Authentication required"
}

function get_workflow_stats() {
  echo "📊 Workflow Statistics:"
  curl -s http://localhost:8000/api/workflows/statistics 2>/dev/null || echo "⚠️  Authentication required"
}

function get_workflow_performance() {
  echo "⚡ Performance Metrics (30 days):"
  curl -s "http://localhost:8000/api/workflows/performance?range=30d" 2>/dev/null || echo "⚠️  Authentication required"
}

function get_execution_details() {
  local execution_id=$1
  if [ -z "$execution_id" ]; then
    echo "❌ Usage: get_execution_details <execution_id>"
    return 1
  fi
  echo "🔎 Execution Details for $execution_id:"
  curl -s http://localhost:8000/api/workflow/$execution_id/details 2>/dev/null || echo "⚠️  Authentication required"
}

# ============================================================================
# AUTHENTICATION HELPERS
# ============================================================================

function get_github_oauth_url() {
  local client_id=${GH_OAUTH_CLIENT_ID:-""}
  if [ -z "$client_id" ]; then
    echo "❌ GH_OAUTH_CLIENT_ID not set"
    return 1
  fi
  echo "🔗 GitHub OAuth URL:"
  echo "https://github.com/login/oauth/authorize?client_id=$client_id&scope=user:email&state=random-state"
}

function generate_demo_jwt() {
  # This generates a simple demo JWT (not production-safe)
  # For testing only!
  echo "🚀 Generating demo JWT token..."
  python3 << 'EOF'
import json
import base64
from datetime import datetime, timedelta

header = {"alg": "HS256", "typ": "JWT"}
payload = {
  "user_id": "test-user-123",
  "username": "test-user",
  "email": "test@example.com",
  "ia_provider": "jwt",
  "is_active": True,
  "created_at": datetime.now().isoformat(),
  "exp": (datetime.now() + timedelta(hours=24)).timestamp()
}

def b64_encode(data):
  return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()

token = f"{b64_encode(header)}.{b64_encode(payload)}.demo-signature"
print("Demo JWT (for testing only):")
print(token)
print("\nUsage: export AUTH_TOKEN='<token>' then use -H 'Authorization: Bearer $AUTH_TOKEN'")
EOF
}

# ============================================================================
# WORKFLOW TESTING SCENARIOS
# ============================================================================

function test_social_media_workflow() {
  echo "🐦 Testing Social Media Workflow (5 min)..."
  echo "Creating custom workflow..."
  
  curl -s -X POST http://localhost:8000/api/workflows/custom \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Quick Social Media Test",
      "description": "Testing social media workflow via API",
      "phases": [
        {
          "name": "draft",
          "agent": "content_agent",
          "timeout_seconds": 60
        }
      ],
      "task_input": {
        "topic": "AI Orchestration Benefits",
        "platform": "twitter",
        "tone": "professional"
      }
    }' | python3 -m json.tool
}

function test_blog_workflow() {
  echo "📝 Testing Blog Post Workflow (15 min)..."
  echo "Creating custom workflow..."
  
  curl -s -X POST http://localhost:8000/api/workflows/custom \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Blog Post Generation Test",
      "description": "Testing comprehensive blog workflow",
      "phases": [
        {
          "name": "research",
          "agent": "content_agent",
          "timeout_seconds": 120
        },
        {
          "name": "draft",
          "agent": "content_agent",
          "timeout_seconds": 180
        }
      ],
      "task_input": {
        "topic": "Future of AI Orchestration",
        "style": "technical",
        "tone": "thought-leadership"
      }
    }' | python3 -m json.tool
}

function test_email_workflow() {
  echo "✉️  Testing Email Workflow (4 min)..."
  echo "Creating custom workflow..."
  
  curl -s -X POST http://localhost:8000/api/workflows/custom \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Email Campaign Test",
      "description": "Testing email workflow",
      "phases": [
        {
          "name": "draft",
          "agent": "content_agent",
          "timeout_seconds": 60
        }
      ],
      "task_input": {
        "topic": "New AI Features Announcement",
        "style": "narrative",
        "tone": "professional"
      }
    }' | python3 -m json.tool
}

# ============================================================================
# QUALITY ASSESSMENT TESTING
# ============================================================================

function check_quality_assessment() {
  local execution_id=$1
  if [ -z "$execution_id" ]; then
    echo "❌ Usage: check_quality_assessment <execution_id>"
    return 1
  fi
  
  echo "🎯 Quality Assessment for $execution_id:"
  curl -s http://localhost:8000/api/workflow/$execution_id/quality \
    2>/dev/null || echo "⚠️  Authentication required"
}

function analyze_quality_trends() {
  echo "📈 Analyzing quality trends..."
  
  # Get last 20 completed workflows and extract quality scores
  python3 << 'EOF'
import subprocess
import json

# Try to get workflow history
try:
  result = subprocess.run(
    ['curl', '-s', 'http://localhost:8000/api/workflows/history?limit=20'],
    capture_output=True,
    text=True
  )
  
  if "Unauthorized" in result.stdout or "Authentication" in result.stdout:
    print("⚠️  Authentication required to fetch workflow history")
    return
  
  data = json.loads(result.stdout)
  if 'executions' not in data:
    print("❌ No executions found")
    return
  
  executions = data['executions']
  completed = [e for e in executions if e.get('status') == 'COMPLETED']
  
  if not completed:
    print("No completed workflows to analyze")
    return
  
  scores = [e.get('quality_score', 0) for e in completed if e.get('quality_score')]
  if not scores:
    print("No quality scores available")
    return
  
  avg_score = sum(scores) / len(scores)
  min_score = min(scores)
  max_score = max(scores)
  
  print(f"✅ Quality Analysis:")
  print(f"  Total completed: {len(completed)}")
  print(f"  Average score: {avg_score:.2f}")
  print(f"  Min score: {min_score:.2f}")
  print(f"  Max score: {max_score:.2f}")
  print(f"  Passing rate (>0.70): {sum(1 for s in scores if s > 0.70) / len(scores) * 100:.1f}%")
  
except Exception as e:
  print(f"❌ Error: {e}")
EOF
}

# ============================================================================
# INTERACTIVE MENU
# ============================================================================

function show_menu() {
  echo ""
  echo "╔════════════════════════════════════════════════════╗"
  echo "║   Oversight Hub Workflow Testing Quick Reference   ║"
  echo "╚════════════════════════════════════════════════════╝"
  echo ""
  echo "BASIC CHECKS:"
  echo "  1. Check backend health"
  echo "  2. List workflow templates"
  echo "  3. Get workflow history"
  echo ""
  echo "AUTHENTICATION:"
  echo "  4. Get GitHub OAuth URL"
  echo "  5. Generate demo JWT token"
  echo ""
  echo "WORKFLOW TESTS:"
  echo "  6. Test social media workflow (quick)"
  echo "  7. Test blog post workflow (comprehensive)"
  echo "  8. Test email workflow (marketing)"
  echo ""
  echo "QUALITY ASSESSMENT:"
  echo "  9. Check quality assessment for workflow"
  echo "  10. Analyze quality trends"
  echo ""
  echo "  0. Exit"
  echo ""
}

function main() {
  if [ "$1" == "batch" ]; then
    # Run all checks in batch mode
    echo "🚀 Running batch test suite..."
    check_backend_health
    echo ""
    list_workflow_templates
    echo ""
    test_social_media_workflow
    echo ""
    analyze_quality_trends
    return
  fi
  
  # Interactive mode
  while true; do
    show_menu
    read -p "Select option: " choice
    
    case $choice in
      1) check_backend_health ;;
      2) list_workflow_templates ;;
      3) get_workflow_history ;;
      4) get_github_oauth_url ;;
      5) generate_demo_jwt ;;
      6) test_social_media_workflow ;;
      7) test_blog_workflow ;;
      8) test_email_workflow ;;
      9) 
        read -p "Enter execution ID: " exec_id
        check_quality_assessment "$exec_id"
        ;;
      10) analyze_quality_trends ;;
      0) 
        echo "👋 Goodbye!"
        exit 0
        ;;
      *)
        echo "❌ Invalid option"
        ;;
    esac
    
    echo ""
    read -p "Press Enter to continue..."
  done
}

# ============================================================================
# COMMAND LINE USAGE
# ============================================================================

if [ $# -eq 0 ]; then
  main
elif [ "$1" == "batch" ]; then
  main batch
elif [ "$1" == "health" ]; then
  check_backend_health
elif [ "$1" == "templates" ]; then
  list_workflow_templates
elif [ "$1" == "history" ]; then
  get_workflow_history
elif [ "$1" == "stats" ]; then
  get_workflow_stats
elif [ "$1" == "performance" ]; then
  get_workflow_performance
elif [ "$1" == "details" ]; then
  get_execution_details "$2"
elif [ "$1" == "social" ]; then
  test_social_media_workflow
elif [ "$1" == "blog" ]; then
  test_blog_workflow
elif [ "$1" == "email" ]; then
  test_email_workflow
elif [ "$1" == "quality" ]; then
  check_quality_assessment "$2"
elif [ "$1" == "trends" ]; then
  analyze_quality_trends
elif [ "$1" == "github-url" ]; then
  get_github_oauth_url
elif [ "$1" == "demo-jwt" ]; then
  generate_demo_jwt
else
  echo "Usage: $0 [command]"
  echo ""
  echo "Commands:"
  echo "  health              Check backend health"
  echo "  templates           List available workflow templates"
  echo "  history             Get workflow execution history"
  echo "  stats               Get workflow statistics"
  echo "  performance         Get performance metrics"
  echo "  social              Test social media workflow"
  echo "  blog                Test blog post workflow"
  echo "  email               Test email workflow"
  echo "  batch               Run all tests in batch mode"
  echo "  details <id>        Get details for specific execution"
  echo "  quality <id>        Check quality assessment"
  echo "  trends              Analyze quality trends"
  echo "  github-url          Get GitHub OAuth URL"
  echo "  demo-jwt            Generate demo JWT token"
  echo ""
  echo "Run without arguments for interactive menu"
fi

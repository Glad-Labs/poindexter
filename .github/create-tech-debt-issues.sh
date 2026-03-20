#!/bin/bash
# Create GitHub issues for technical debt tracking
# Usage: bash .github/create-tech-debt-issues.sh
# Note: Requires GitHub CLI (https://cli.github.com) and authentication

REPO=${1:-"mattm/glad-labs-website"}
JSON_FILE=".github/tech-debt-issues.json"

if ! command -v jq &> /dev/null; then
    echo "❌ 'jq' is required but not installed. Install it first:"
    echo "   macOS: brew install jq"
    echo "   Linux: apt-get install jq"
    echo "   Windows: choco install jq"
    exit 1
fi

if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI is required. Install from: https://cli.github.com"
    exit 1
fi

# Check authentication
if ! gh auth status &> /dev/null; then
    echo "❌ Not authenticated with GitHub. Run: gh auth login"
    exit 1
fi

echo "📋 Creating GitHub issues from $JSON_FILE..."
echo "Repository: $REPO"
echo ""

# Read JSON and create issues
jq -r '.issues[] | @base64 | @uri' "$JSON_FILE" | while IFS= read -r issue_b64; do
    issue=$(echo "$issue_b64" | base64 -d)
    
    number=$(echo "$issue" | jq -r '.number')
    title=$(echo "$issue" | jq -r '.title')
    description=$(echo "$issue" | jq -r '.description')
    files=$(echo "$issue" | jq -r '.files | join(", ")')
    effort=$(echo "$issue" | jq -r '.effort_hours')
    labels=$(echo "$issue" | jq -r '.labels | join(",")')
    
    # Build acceptance criteria
    criteria=$(echo "$issue" | jq -r '.acceptance_criteria | map("- [ ] \(.)") | join("\n")')
    
    # Build body
    body="## Description
$description

## Files Affected
$files

## Effort Estimate
$effort hours

## Acceptance Criteria
$criteria

## Labels
$labels
"

    echo "Creating issue #$number: $title"
    
    # Create the issue
    gh issue create \
        --repo "$REPO" \
        --title "$title" \
        --body "$body" \
        --label "$(echo "$labels" | tr ',' ' ')" 2>/dev/null || echo "  ⚠️  Could not create (may already exist)"
done

echo ""
echo "✅ Issue creation complete! Check: https://github.com/$REPO/issues"

#!/bin/bash
ACTION="${1:-status}"

case "$ACTION" in
  status)
    vercel inspect 2>&1 | head -20
    ;;
  deployments)
    vercel ls --limit 5 2>&1
    ;;
  domains)
    vercel domains ls 2>&1
    ;;
  *)
    echo "=== Vercel Deployments ==="
    vercel ls --limit 5 2>&1
    echo ""
    echo "=== Domains ==="
    vercel domains ls 2>&1
    ;;
esac

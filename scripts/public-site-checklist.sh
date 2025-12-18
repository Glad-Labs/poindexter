#!/bin/bash

# 📋 Public Site Launch Checklist
# Use this to track your progress getting the site to production

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Checklist item counter
ITEMS=0
COMPLETED=0

check_item() {
  local number=$1
  local description=$2
  local status=$3
  
  if [ "$status" = "done" ]; then
    echo -e "${GREEN}✅${NC} $number. $description"
    ((COMPLETED++))
  elif [ "$status" = "skip" ]; then
    echo -e "${YELLOW}⏭️${NC}  $number. $description (skipped)"
  else
    echo -e "${RED}⬜${NC} $number. $description"
  fi
  ((ITEMS++))
}

header() {
  echo ""
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${BLUE}$1${NC}"
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
}

main() {
  clear

  echo ""
  echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║   📋 PUBLIC SITE LAUNCH CHECKLIST        ║${NC}"
  echo -e "${BLUE}║   Glad Labs Blog - Getting to Production ║${NC}"
  echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
  echo ""

  # ===== PHASE 1: PREPARATION =====
  header "PHASE 1: PREPARATION"
  
  check_item "1.1" "Review PUBLIC_SITE_EXECUTIVE_SUMMARY.md" "$1"
  check_item "1.2" "Review PUBLIC_SITE_INTEGRATION_GUIDE.md" "$2"
  check_item "1.3" "Review PUBLIC_SITE_PRODUCTION_READINESS.md" "$3"
  check_item "1.4" "Database: Verify PostgreSQL is running" "$4"
  check_item "1.5" "Backend: Verify FastAPI server is running" "$5"
  check_item "1.6" "Check: Database has 8 published posts" "$6"

  # ===== PHASE 2: INTEGRATION =====
  header "PHASE 2: FRONTEND INTEGRATION (30 MIN)"
  
  check_item "2.1" "Update api-fastapi.js: Add mapper import" "$7"
  check_item "2.2" "Update api-fastapi.js: Update getPaginatedPosts()" "$8"
  check_item "2.3" "Update api-fastapi.js: Update getFeaturedPost()" "$9"
  check_item "2.4" "Update pages/index.js: Remove Strapi references" "${10}"
  check_item "2.5" "Update pages/index.js: Add proper image handling" "${11}"
  check_item "2.6" "Test: npm run dev and verify posts display" "${12}"
  check_item "2.7" "Test: Check browser console for errors" "${13}"
  check_item "2.8" "Test: Verify featured images load (if set)" "${14}"
  check_item "2.9" "Test: Verify links navigate correctly" "${15}"

  # ===== PHASE 3: IMAGES =====
  header "PHASE 3: FEATURED IMAGES (30 MIN)"
  
  echo "Choose your image approach:"
  echo ""
  echo -e "${YELLOW}Option A: Quick Placeholder Images${NC}"
  check_item "3.1a" "Run SQL: UPDATE posts SET featured_image_url = ..." "${16}"
  
  echo ""
  echo -e "${YELLOW}Option B: Generate Real Images (30 min - 2 hours)${NC}"
  check_item "3.1b" "Implement /api/media/generate-image endpoint" "${17}"
  check_item "3.2b" "Generate images for all 8 posts" "${18}"
  check_item "3.3b" "Verify images are accessible and loading" "${19}"
  
  echo ""
  echo -e "${YELLOW}Option C: Manual Upload${NC}"
  check_item "3.1c" "Create featured images (Canva/DALL-E/Figma)" "${20}"
  check_item "3.2c" "Upload images to CDN (Cloudinary/Vercel/AWS)" "${21}"
  check_item "3.3c" "Update database with image URLs" "${22}"

  # ===== PHASE 4: CONTENT REVIEW =====
  header "PHASE 4: CONTENT REVIEW (30 MIN)"
  
  check_item "4.1" "Fix: 3 posts titled 'Untitled' - give proper titles" "${23}"
  check_item "4.2" "Verify: All posts are 300+ words" "${24}"
  check_item "4.3" "Verify: SEO metadata is filled in" "${25}"
  check_item "4.4" "Add: AI-generated content disclosure to site" "${26}"
  check_item "4.5" "Review: Post formatting and quality" "${27}"

  # ===== PHASE 5: PRODUCTION BUILD =====
  header "PHASE 5: PRODUCTION BUILD (15 MIN)"
  
  check_item "5.1" "Run: npm run build (should complete without warnings)" "${28}"
  check_item "5.2" "Verify: Build output includes all pages" "${29}"
  check_item "5.3" "Run: npm run start (test production build locally)" "${30}"
  check_item "5.4" "Test: Pages load correctly on production build" "${31}"
  check_item "5.5" "Test: Images load on production build" "${32}"

  # ===== PHASE 6: DEPLOYMENT =====
  header "PHASE 6: DEPLOYMENT"
  
  check_item "6.1" "Deploy: Push code to production repo" "${33}"
  check_item "6.2" "Deploy: Trigger production build (Vercel/Railway/etc)" "${34}"
  check_item "6.3" "Verify: Site is live at https://your-domain.com" "${35}"
  check_item "6.4" "Test: Browse site on production (desktop)" "${36}"
  check_item "6.5" "Test: Browse site on production (mobile)" "${37}"
  check_item "6.6" "Monitor: Check logs for any errors" "${38}"

  # ===== PHASE 7: ANALYTICS & ADSENSE PREP =====
  header "PHASE 7: ANALYTICS & ADSENSE PREP (1-2 HOURS)"
  
  check_item "7.1" "Set up: Google Analytics 4 tracking" "${39}"
  check_item "7.2" "Set up: Google Search Console (verify domain)" "${40}"
  check_item "7.3" "Configure: robots.txt and sitemap.xml" "${41}"
  check_item "7.4" "Create: About page (team, mission, story)" "${42}"
  check_item "7.5" "Create: Contact page (form or email)" "${43}"
  check_item "7.6" "Verify: Privacy policy is legally compliant" "${44}"
  check_item "7.7" "Submit: URL to Google Search Console for crawling" "${45}"

  # ===== PHASE 8: PRE-ADSENSE CHECKLIST =====
  header "PHASE 8: ADSENSE APPLICATION PREP"
  
  check_item "8.1" "Monitor: Wait for Google to crawl pages (3-7 days)" "${46}"
  check_item "8.2" "Check: Verify posts appear in Google search results" "${47}"
  check_item "8.3" "Monitor: Accumulate 1,000+ monthly page views" "${48}"
  check_item "8.4" "Monitor: Ensure 30 days of traffic history" "${49}"
  check_item "8.5" "Review: Content - no policy violations" "${50}"
  check_item "8.6" "Review: No excessive ads or pop-ups" "${51}"
  check_item "8.7" "Apply: Submit to Google AdSense" "${52}"
  check_item "8.8" "Wait: Google approval (typically 2-4 weeks)" "${53}"

  # ===== SUMMARY =====
  header "SUMMARY"
  
  local percentage=$((COMPLETED * 100 / ITEMS))
  echo -e "Progress: ${GREEN}$COMPLETED / $ITEMS${NC} items complete"
  echo -e "Percentage: ${BLUE}$percentage%${NC}"
  echo ""

  if [ $percentage -eq 0 ]; then
    echo -e "${YELLOW}📌 Not started. Follow the phases in order.${NC}"
  elif [ $percentage -lt 25 ]; then
    echo -e "${YELLOW}🚀 Just getting started. Keep going!${NC}"
  elif [ $percentage -lt 50 ]; then
    echo -e "${YELLOW}⚡ Halfway there. You're making progress!${NC}"
  elif [ $percentage -lt 75 ]; then
    echo -e "${YELLOW}🎯 Home stretch. Almost to production!${NC}"
  elif [ $percentage -lt 100 ]; then
    echo -e "${GREEN}✨ Nearly done. Just a few items left!${NC}"
  else
    echo -e "${GREEN}🎉 COMPLETE! All items checked off!${NC}"
  fi

  echo ""
  echo "Key Documents:"
  echo "  📄 PUBLIC_SITE_EXECUTIVE_SUMMARY.md"
  echo "  📄 PUBLIC_SITE_INTEGRATION_GUIDE.md"
  echo "  📄 PUBLIC_SITE_PRODUCTION_READINESS.md"
  echo ""
  echo "Keep this script handy - you can mark items as complete and track progress!"
  echo ""
}

# Run if no arguments or help requested
if [ $# -eq 0 ] || [ "$1" = "--help" ]; then
  echo ""
  echo "Usage: bash public-site-checklist.sh"
  echo ""
  echo "This is an interactive checklist. Use with the integration guide:"
  echo "  cat PUBLIC_SITE_INTEGRATION_GUIDE.md"
  echo ""
  echo "Or follow the automated fix script:"
  echo "  bash scripts/fix-public-site.sh"
  echo ""
  main
else
  main "$@"
fi

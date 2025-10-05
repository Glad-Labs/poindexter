@echo off
echo ========================================================
echo GLAD LABS - Manual Content Type Creation Commands
echo ========================================================
echo.
echo Run these commands ONE AT A TIME in separate terminals:
echo.
echo ========================================================
echo TERMINAL 1: Keep Strapi running
echo ========================================================
echo cd cms/strapi-backend
echo npm run develop
echo.
echo ========================================================
echo TERMINAL 2: Generate content types (run each separately)
echo ========================================================
echo.
echo 1. CREATE AUTHOR CONTENT TYPE:
echo --------------------------------
echo cd cms/strapi-backend
echo npx strapi generate api author
echo.
echo 2. CREATE TAG CONTENT TYPE:
echo ---------------------------
echo cd cms/strapi-backend  
echo npx strapi generate api tag
echo.
echo 3. CREATE CONTENT-METRIC CONTENT TYPE:
echo -------------------------------------
echo cd cms/strapi-backend
echo npx strapi generate api content-metric
echo.
echo ========================================================
echo Alternative: Manual File Creation
echo ========================================================
echo.
echo If the generate commands don't work, run these:
echo.
echo 4. CREATE MISSING CONTROLLER FILES:
echo ----------------------------------
echo.
echo For Author:
echo mkdir "src\api\author\controllers" 2^>nul
echo echo module.exports = {}; ^> "src\api\author\controllers\author.js"
echo.
echo mkdir "src\api\author\services" 2^>nul  
echo echo module.exports = {}; ^> "src\api\author\services\author.js"
echo.
echo mkdir "src\api\author\routes" 2^>nul
echo echo module.exports = {}; ^> "src\api\author\routes\author.js"
echo.
echo For Tag:
echo mkdir "src\api\tag\controllers" 2^>nul
echo echo module.exports = {}; ^> "src\api\tag\controllers\tag.js"
echo.
echo mkdir "src\api\tag\services" 2^>nul
echo echo module.exports = {}; ^> "src\api\tag\services\tag.js"
echo.
echo mkdir "src\api\tag\routes" 2^>nul
echo echo module.exports = {}; ^> "src\api\tag\routes\tag.js"
echo.
echo For Content-metric:
echo mkdir "src\api\content-metric\controllers" 2^>nul
echo echo module.exports = {}; ^> "src\api\content-metric\controllers\content-metric.js"
echo.
echo mkdir "src\api\content-metric\services" 2^>nul
echo echo module.exports = {}; ^> "src\api\content-metric\services\content-metric.js"
echo.
echo mkdir "src\api\content-metric\routes" 2^>nul
echo echo module.exports = {}; ^> "src\api\content-metric\routes\content-metric.js"
echo.
echo ========================================================
echo After running these commands:
echo 1. Strapi will restart automatically
echo 2. Check the permissions page again
echo 3. All content types should appear
echo ========================================================
pause
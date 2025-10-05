@echo off
echo ========================================================
echo NUCLEAR OPTION: Complete Content Type Recreation
echo ========================================================
echo.
echo WARNING: This will delete and recreate content types!
echo Press Ctrl+C to cancel, or any key to continue...
pause >nul
echo.

echo Removing existing content type directories...
rmdir /s /q "src\api\author" 2>nul
rmdir /s /q "src\api\tag" 2>nul  
rmdir /s /q "src\api\content-metric" 2>nul

echo Creating fresh content type structures...

echo.
echo Creating Author...
mkdir "src\api\author\content-types\author"
mkdir "src\api\author\controllers"
mkdir "src\api\author\routes"
mkdir "src\api\author\services"

(
echo {
echo   "kind": "collectionType",
echo   "collectionName": "authors", 
echo   "info": {
echo     "singularName": "author",
echo     "pluralName": "authors",
echo     "displayName": "Author"
echo   },
echo   "options": {
echo     "draftAndPublish": false
echo   },
echo   "attributes": {
echo     "Name": {
echo       "type": "string",
echo       "required": true
echo     }
echo   }
echo }
) > "src\api\author\content-types\author\schema.json"

echo module.exports = require('@strapi/strapi'^).factories.createCoreController('api::author.author'^); > "src\api\author\controllers\author.js"
echo module.exports = require('@strapi/strapi'^).factories.createCoreService('api::author.author'^); > "src\api\author\services\author.js"  
echo module.exports = require('@strapi/strapi'^).factories.createCoreRouter('api::author.author'^); > "src\api\author\routes\author.js"

echo.
echo Creating Tag...
mkdir "src\api\tag\content-types\tag"
mkdir "src\api\tag\controllers"
mkdir "src\api\tag\routes"
mkdir "src\api\tag\services"

(
echo {
echo   "kind": "collectionType",
echo   "collectionName": "tags",
echo   "info": {
echo     "singularName": "tag",
echo     "pluralName": "tags", 
echo     "displayName": "Tag"
echo   },
echo   "options": {
echo     "draftAndPublish": false
echo   },
echo   "attributes": {
echo     "Name": {
echo       "type": "string",
echo       "required": true
echo     }
echo   }
echo }
) > "src\api\tag\content-types\tag\schema.json"

echo module.exports = require('@strapi/strapi'^).factories.createCoreController('api::tag.tag'^); > "src\api\tag\controllers\tag.js"
echo module.exports = require('@strapi/strapi'^).factories.createCoreService('api::tag.tag'^); > "src\api\tag\services\tag.js"
echo module.exports = require('@strapi/strapi'^).factories.createCoreRouter('api::tag.tag'^); > "src\api\tag\routes\tag.js"

echo.
echo ✅ Basic content types recreated!
echo Strapi should restart and show Author and Tag in permissions.
echo Check the permissions page now!
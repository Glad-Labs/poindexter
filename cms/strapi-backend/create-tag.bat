@echo off
echo Creating Tag content type structure...

echo Creating controller...
mkdir "src\api\tag\controllers" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreController('api::tag.tag'); > "src\api\tag\controllers\tag.js"

echo Creating service...
mkdir "src\api\tag\services" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreService('api::tag.tag'); > "src\api\tag\services\tag.js"

echo Creating routes...
mkdir "src\api\tag\routes" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreRouter('api::tag.tag'); > "src\api\tag\routes\tag.js"

echo ✅ Tag content type structure created!
echo Strapi should restart automatically...
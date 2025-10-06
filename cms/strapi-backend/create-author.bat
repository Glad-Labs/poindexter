@echo off
echo Creating Author content type structure...

echo Creating controller...
mkdir "src\api\author\controllers" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreController('api::author.author'); > "src\api\author\controllers\author.js"

echo Creating service...
mkdir "src\api\author\services" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreService('api::author.author'); > "src\api\author\services\author.js"

echo Creating routes...
mkdir "src\api\author\routes" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreRouter('api::author.author'); > "src\api\author\routes\author.js"

echo ✅ Author content type structure created!
echo Strapi should restart automatically...
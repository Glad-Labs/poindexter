@echo off
echo Creating Content-metric content type structure...

echo Creating controller...
mkdir "src\api\content-metric\controllers" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreController('api::content-metric.content-metric'); > "src\api\content-metric\controllers\content-metric.js"

echo Creating service...
mkdir "src\api\content-metric\services" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreService('api::content-metric.content-metric'); > "src\api\content-metric\services\content-metric.js"

echo Creating routes...
mkdir "src\api\content-metric\routes" 2>nul
echo module.exports = require('@strapi/strapi').factories.createCoreRouter('api::content-metric.content-metric'); > "src\api\content-metric\routes\content-metric.js"

echo ✅ Content-metric content type structure created!
echo Strapi should restart automatically...
@echo off
echo Testing content type recognition...
echo.
echo This will temporarily simplify the author schema to test if relationships are causing issues.
echo.

echo Creating backup of current author schema...
copy "src\api\author\content-types\author\schema.json" "src\api\author\content-types\author\schema.json.backup"

echo Creating simplified author schema...
(
echo {
echo   "kind": "collectionType",
echo   "collectionName": "authors",
echo   "info": {
echo     "singularName": "author",
echo     "pluralName": "authors",
echo     "displayName": "Author",
echo     "description": "Represents content authors"
echo   },
echo   "options": {
echo     "draftAndPublish": false
echo   },
echo   "pluginOptions": {},
echo   "attributes": {
echo     "Name": {
echo       "type": "string",
echo       "required": true
echo     },
echo     "Bio": {
echo       "type": "text"
echo     }
echo   }
echo }
) > "src\api\author\content-types\author\schema.json"

echo ✅ Simplified author schema created!
echo Strapi should restart and you should see Author in permissions.
echo.
echo To restore original schema later, run:
echo copy "src\api\author\content-types\author\schema.json.backup" "src\api\author\content-types\author\schema.json"
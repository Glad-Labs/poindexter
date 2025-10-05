@echo off
echo Triggering Strapi to recognize content types...

echo Touching author schema...
copy /Y "src\api\author\content-types\author\schema.json" "src\api\author\content-types\author\schema.json.bak" >nul
copy /Y "src\api\author\content-types\author\schema.json.bak" "src\api\author\content-types\author\schema.json" >nul
del "src\api\author\content-types\author\schema.json.bak" >nul

echo Touching tag schema...
copy /Y "src\api\tag\content-types\tag\schema.json" "src\api\tag\content-types\tag\schema.json.bak" >nul
copy /Y "src\api\tag\content-types\tag\schema.json.bak" "src\api\tag\content-types\tag\schema.json" >nul
del "src\api\tag\content-types\tag\schema.json.bak" >nul

echo Touching content-metric schema...
copy /Y "src\api\content-metric\content-types\content-metric\schema.json" "src\api\content-metric\content-types\content-metric\schema.json.bak" >nul
copy /Y "src\api\content-metric\content-types\content-metric\schema.json.bak" "src\api\content-metric\content-types\content-metric\schema.json" >nul
del "src\api\content-metric\content-types\content-metric\schema.json.bak" >nul

echo Done! Strapi should restart and recognize the content types.
@echo off
echo ==================================================
echo GLAD LABS - Content Type Test Script
echo ==================================================
echo.
echo Testing if all content types are available...
echo.

echo Testing Categories:
curl -s "http://localhost:1337/api/categories" | findstr "data"
echo.

echo Testing Posts:
curl -s "http://localhost:1337/api/posts" | findstr "data"
echo.

echo Testing Authors:
curl -s "http://localhost:1337/api/authors" | findstr "data"
echo.

echo Testing Tags:
curl -s "http://localhost:1337/api/tags" | findstr "data"
echo.

echo Testing Content-metrics:
curl -s "http://localhost:1337/api/content-metrics" | findstr "data"
echo.

echo ==================================================
echo Test complete! 
echo If you see "data" responses, the content types are working.
echo If you see "Forbidden" errors, permissions need to be configured.
echo ==================================================
pause
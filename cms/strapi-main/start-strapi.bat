@echo off
REM Start Strapi CMS with proper node module resolution
REM This script sets NODE_PATH to find modules in multiple locations

cd /d "%~dp0"

set NODE_PATH=%cd%\node_modules;%cd%\..\..node_modules;%NODE_PATH%

REM Run strapi using npx
call npx strapi develop

pause

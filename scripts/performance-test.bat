@echo off
REM Performance Testing Script for Oversight Hub (Windows)
REM Runs automated tests, captures metrics, and generates report

echo.
echo 🔬 Glad Labs Oversight Hub - Performance Testing Suite
echo ======================================================
echo.

REM Check if services are running
echo 📡 Checking service health...

curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel% equ 0 (
    echo [✓] Backend API ^(port 8000^) is running
) else (
    echo [✗] Backend API not responding
    echo     Start with: npm run dev:cofounder
    exit /b 1
)

curl -s http://localhost:3001 >nul 2>&1
if %errorlevel% equ 0 (
    echo [✓] Oversight Hub ^(port 3001^) is running
) else (
    echo [✗] Oversight Hub not responding
    echo     Start with: npm run dev:oversight
    exit /b 1
)

echo.
echo 🎯 Starting Performance Tests...
echo.

REM Create results directory
if not exist "test-results\performance" mkdir test-results\performance
if not exist "test-results\screenshots" mkdir test-results\screenshots

REM Test 1: API Response Times
echo ⏱️  Test 1: API Response Times
echo    Measuring backend endpoint latency...

for %%e in (/health /api/tasks /api/agents /api/analytics) do (
    echo    Testing %%e ...
    curl -s -w "   Response time: %%{time_total}s\n" -o nul http://localhost:8000%%e
)

echo.

REM Test 2: Frontend Load Performance
echo 🌐 Test 2: Frontend Load Performance
echo    Measuring page load times...

for %%p in (/ /tasks /approvals /services) do (
    echo    Testing %%p ...
    curl -s -w "   Load time: %%{time_total}s\n" -o nul http://localhost:3001%%p
)

echo.

REM Test 3: Run Playwright Tests
echo 🎭 Test 3: Automated UI Tests ^(Playwright^)
echo    Running full test suite...
echo.

npx playwright test --config playwright.oversight.config.ts

if %errorlevel% equ 0 (
    echo [✓] All Playwright tests passed
) else (
    echo [✗] Some tests failed - check report
)

echo.

REM Generate HTML Report
echo 📊 Generating Performance Report...

(
echo ^<!DOCTYPE html^>
echo ^<html^>
echo ^<head^>
echo     ^<title^>Oversight Hub - Performance Test Report^</title^>
echo     ^<style^>
echo         body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
echo         .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba^(0,0,0,0.1^); }
echo         h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
echo         h2 { color: #34495e; margin-top: 30px; }
echo         .metric { display: inline-block; margin: 10px 20px; padding: 15px; background: #ecf0f1; border-radius: 5px; }
echo         .metric-label { font-size: 12px; color: #7f8c8d; text-transform: uppercase; }
echo         .metric-value { font-size: 24px; font-weight: bold; color: #2c3e50; }
echo         .pass { color: #27ae60; }
echo         table { width: 100%%; border-collapse: collapse; margin: 20px 0; }
echo         th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
echo         th { background: #3498db; color: white; }
echo         tr:hover { background: #f5f5f5; }
echo     ^</style^>
echo ^</head^>
echo ^<body^>
echo     ^<div class="container"^>
echo         ^<h1^>🔬 Oversight Hub Performance Test Report^</h1^>
echo         ^<p^>Generated: %date% %time%^</p^>
echo         ^<h2^>Summary^</h2^>
echo         ^<div class="metric"^>^<div class="metric-label"^>Backend Status^</div^>^<div class="metric-value pass"^>✓ Running^</div^>^</div^>
echo         ^<div class="metric"^>^<div class="metric-label"^>Frontend Status^</div^>^<div class="metric-value pass"^>✓ Running^</div^>^</div^>
echo         ^<h2^>Results^</h2^>
echo         ^<p^>✅ All services operational^</p^>
echo         ^<p^>📊 Check Playwright report for detailed test results^</p^>
echo         ^<p^>📁 Screenshots available in test-results/screenshots/^</p^>
echo     ^</div^>
echo ^</body^>
echo ^</html^>
) > test-results\performance\report.html

echo [✓] Performance report generated: test-results\performance\report.html

echo.
echo ======================================================
echo ✅ Performance Testing Complete!
echo.
echo 📁 Results saved to:
echo    • test-results\performance\report.html ^(Open in browser^)
echo    • oversight-report\ ^(Playwright HTML report^)
echo    • test-results\screenshots\ ^(UI screenshots^)
echo.
echo 📖 For detailed testing guide, see: USER_TESTING_GUIDE.md
echo.
echo 🚀 To view Playwright report:
echo    npx playwright show-report oversight-report
echo.

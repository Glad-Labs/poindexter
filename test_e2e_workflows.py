#!/usr/bin/env python3
"""
End-to-End Workflow Testing for Glad Labs
Tests critical workflows and reports any issues

FIXES in this version:
- Added JWT authentication for /api/tasks endpoint
- Improved error messages and diagnostics
- Better handling of auth-protected endpoints
"""

import requests
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
from dataclasses import dataclass

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    jwt = None  # type: ignore
    print("[WARNING] PyJWT not installed. Installing might help with auth tests.")

@dataclass
class TestResult:
    name: str
    status: str  # "PASS", "FAIL", "WARN"
    message: str
    details: str = ""

class GladLabsE2ETester:
    def __init__(self):
        self.backend_url = "http://localhost:8000"
        self.results: List[TestResult] = []
        self.session = requests.Session()
        self.jwt_token = self._generate_jwt_token() if JWT_AVAILABLE else ""
        
    def _generate_jwt_token(self) -> str:
        """Generate a test JWT token for authentication"""
        if not JWT_AVAILABLE or jwt is None:
            return ""
        try:
            # Use same secret as backend
            secret = os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET") or "dev-jwt-secret-change-in-production-to-random-64-chars"
            payload = {
                "sub": "test-user",
                "user_id": "test-user-id",
                "email": "test@example.com",
                "username": "test-user",
                "type": "access",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            }
            token = jwt.encode(payload, secret, algorithm="HS256")  # type: ignore
            print(f"[INFO] Generated JWT token for testing")
            return token
        except Exception as e:
            print(f"[ERROR] Failed to generate JWT: {str(e)}")
            return ""
        
    def test_backend_health(self) -> TestResult:
        """Test backend health endpoint"""
        try:
            response = self.session.get(f"{self.backend_url}/health")
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    return TestResult(
                        "Backend Health",
                        "PASS",
                        "Backend is healthy and responding"
                    )
            return TestResult("Backend Health", "FAIL", "Unexpected response", str(response.json()))
        except Exception as e:
            return TestResult("Backend Health", "FAIL", f"Connection failed: {str(e)}")
    
    def test_cms_get_posts(self) -> TestResult:
        """Test CMS posts endpoint"""
        try:
            response = self.session.get(f"{self.backend_url}/api/posts")
            if response.status_code == 200:
                data = response.json()
                if "data" in data and isinstance(data["data"], list):
                    count = len(data["data"])
                    return TestResult(
                        "CMS Get Posts",
                        "PASS",
                        f"Retrieved {count} posts successfully"
                    )
                return TestResult("CMS Get Posts", "FAIL", "Invalid response format")
            return TestResult("CMS Get Posts", "FAIL", f"Status {response.status_code}")
        except Exception as e:
            return TestResult("CMS Get Posts", "FAIL", f"Request failed: {str(e)}")
    
    def test_cms_get_categories(self) -> TestResult:
        """Test CMS categories endpoint"""
        try:
            response = self.session.get(f"{self.backend_url}/api/categories")
            if response.status_code == 200:
                data = response.json()
                if "data" in data and isinstance(data["data"], list):
                    count = len(data["data"])
                    return TestResult(
                        "CMS Get Categories",
                        "PASS",
                        f"Retrieved {count} categories successfully"
                    )
                return TestResult("CMS Get Categories", "FAIL", "Invalid response format")
            return TestResult("CMS Get Categories", "FAIL", f"Status {response.status_code}")
        except Exception as e:
            return TestResult("CMS Get Categories", "FAIL", f"Request failed: {str(e)}")
    
    def test_cms_get_tags(self) -> TestResult:
        """Test CMS tags endpoint (FIXED - no more color column error)"""
        try:
            response = self.session.get(f"{self.backend_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                if "data" in data and isinstance(data["data"], list):
                    count = len(data["data"])
                    return TestResult(
                        "CMS Get Tags",
                        "PASS",
                        f"Retrieved {count} tags successfully (FIXED)"
                    )
                return TestResult("CMS Get Tags", "FAIL", "Invalid response format")
            return TestResult("CMS Get Tags", "FAIL", f"Status {response.status_code}")
        except Exception as e:
            return TestResult("CMS Get Tags", "FAIL", f"Request failed: {str(e)}")
    
    def test_task_list(self) -> TestResult:
        """Test task list endpoint with JWT authentication"""
        try:
            headers = {"Content-Type": "application/json"}
            if self.jwt_token:
                headers["Authorization"] = f"Bearer {self.jwt_token}"
            
            response = self.session.get(
                f"{self.backend_url}/api/tasks",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "tasks" in data and isinstance(data["tasks"], list):
                    count = len(data["tasks"])
                    total = data.get("total", count)
                    return TestResult(
                        "Task List",
                        "PASS",
                        f"Retrieved {count}/{total} tasks with JWT auth"
                    )
                return TestResult("Task List", "FAIL", "Invalid response format - missing 'tasks' key", str(list(data.keys()))[:200])
            elif response.status_code == 401:
                auth_detail = "JWT token generated but rejected by backend" if self.jwt_token else "No JWT token generated"
                return TestResult(
                    "Task List",
                    "FAIL",
                    f"401 Unauthorized - {auth_detail}",
                    str(response.json())[:300]
                )
            else:
                return TestResult(
                    "Task List",
                    "FAIL",
                    f"HTTP {response.status_code}",
                    str(response.json())[:200]
                )
        except Exception as e:
            return TestResult("Task List", "FAIL", f"Request failed: {str(e)}")
    
    def test_analytics_kpis(self) -> TestResult:
        """Test analytics KPI endpoint"""
        try:
            headers = {"Content-Type": "application/json"}
            if self.jwt_token:
                headers["Authorization"] = f"Bearer {self.jwt_token}"
            
            response = self.session.get(
                f"{self.backend_url}/api/analytics/kpis",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                # Check for expected KPI fields
                if "total_tasks" in data and "success_rate" in data:
                    total_tasks = data.get("total_tasks", 0)
                    success_rate = data.get("success_rate", 0)
                    return TestResult(
                        "Analytics KPIs",
                        "PASS",
                        f"Retrieved KPIs - Total tasks: {total_tasks}, Success rate: {success_rate}%"
                    )
                return TestResult("Analytics KPIs", "FAIL", f"Missing expected KPI fields. Keys: {list(data.keys())[:5]}")
            return TestResult("Analytics KPIs", "FAIL", f"Status {response.status_code}")
        except Exception as e:
            return TestResult("Analytics KPIs", "FAIL", f"Request failed: {str(e)}")
    
    def test_ollama_health(self) -> TestResult:
        """Test Ollama health endpoint"""
        try:
            response = self.session.get(f"{self.backend_url}/api/ollama/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                status = data.get("status", "unknown")
                if status == "running":
                    return TestResult(
                        "Ollama Health",
                        "PASS",
                        "Ollama is running"
                    )
                else:
                    return TestResult(
                        "Ollama Health",
                        "WARN",
                        f"Ollama status: {status}"
                    )
            return TestResult("Ollama Health", "WARN", f"Status {response.status_code}")
        except Exception as e:
            return TestResult("Ollama Health", "WARN", f"Ollama unavailable: {str(e)}")
    
    def run_all_tests(self):
        """Run all tests"""
        print("=" * 70)
        print("GLAD LABS END-TO-END WORKFLOW TEST")
        print("=" * 70)
        print()
        
        if not self.jwt_token and JWT_AVAILABLE:
            print("[WARNING] JWT token generation failed. Task tests will likely fail.")
            print()
        
        tests = [
            self.test_backend_health,
            self.test_cms_get_posts,
            self.test_cms_get_categories,
            self.test_cms_get_tags,
            self.test_task_list,
            self.test_analytics_kpis,
            self.test_ollama_health,
        ]
        
        for test in tests:
            result = test()
            self.results.append(result)
            self.print_result(result)
        
        print()
        self.print_summary()
    
    def print_result(self, result: TestResult):
        """Print a test result"""
        status_symbol = "✅" if result.status == "PASS" else "⚠️ " if result.status == "WARN" else "❌"
        print(f"{status_symbol} {result.name}")
        print(f"   └─ {result.message}")
        if result.details:
            print(f"      {result.details[:80]}")
    
    def print_summary(self):
        """Print test summary"""
        passed = sum(1 for r in self.results if r.status == "PASS")
        warned = sum(1 for r in self.results if r.status == "WARN")
        failed = sum(1 for r in self.results if r.status == "FAIL")
        total = len(self.results)
        
        print("=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)
        print(f"✅ PASSED: {passed}/{total}")
        print(f"⚠️  WARNED: {warned}/{total}")
        print(f"❌ FAILED: {failed}/{total}")
        print()
        
        if failed == 0:
            if warned == 0:
                print("🎉 ALL TESTS PASSED!")
            else:
                print("✅ CRITICAL TESTS PASSED (with warnings)")
        else:
            print(f"❌ {failed} TEST(S) FAILED")
            print("\nFailed tests:")
            for result in self.results:
                if result.status == "FAIL":
                    print(f"  - {result.name}: {result.message}")
        
        print()

if __name__ == "__main__":
    tester = GladLabsE2ETester()
    tester.run_all_tests()

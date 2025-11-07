#!/usr/bin/env python3
"""
End-to-End Ollama Pipeline Testing & Quality Validation

Complete testing workflow:
1. Start Ollama connectivity test
2. Generate content with multiple models
3. Assess quality of generated content
4. Compare output across models
5. Generate comprehensive report
6. Test via FastAPI backend endpoints
7. Persist results to database

Usage:
    python test_ollama_e2e.py
    
Expected output:
    - Real-time generation metrics
    - Quality assessment for each generation
    - Model comparison report
    - Backend API integration test
    - Saved results JSON file
"""

import asyncio
import httpx
import json
import time
from typing import Dict, List, Any
from datetime import datetime
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import test utilities
try:
    from tests.test_ollama_generation_pipeline import OllamaGenerationTester
    from tests.test_quality_assessor import QualityAssessor, generate_quality_report
except ImportError as e:
    print(f"⚠️ Import warning: {e}")
    print("Some features may not be available")


class OllamaPipelineTester:
    """End-to-end pipeline tester"""

    def __init__(self):
        self.ollama_base_url = "http://localhost:11434"
        self.backend_base_url = "http://localhost:8000"
        self.results = {
            'timestamp': datetime.now().isoformat(),
            'tests': [],
            'models_tested': {},
            'quality_assessments': [],
            'backend_integration': {},
            'summary': {}
        }

    async def run_full_pipeline(self):
        """Run complete end-to-end pipeline"""
        print("\n" + "=" * 100)
        print("🚀 OLLAMA GENERATION PIPELINE - FULL E2E TEST")
        print("=" * 100 + "\n")

        # Test 1: Ollama Connectivity
        print("📋 STEP 1: Testing Ollama Connectivity")
        print("─" * 100)
        connectivity_ok = await self._test_connectivity()
        if not connectivity_ok:
            print("❌ Ollama not available. Aborting tests.")
            return

        # Test 2: Generate Content with Multiple Models
        print("\n📋 STEP 2: Generating Content with Multiple Models")
        print("─" * 100)
        generation_results = await self._test_content_generation()

        # Test 3: Quality Assessment
        print("\n📋 STEP 3: Assessing Quality of Generated Content")
        print("─" * 100)
        quality_results = await self._assess_content_quality(generation_results)

        # Test 4: Backend Integration
        print("\n📋 STEP 4: Testing Backend API Integration")
        print("─" * 100)
        backend_results = await self._test_backend_integration()

        # Test 5: Generate Reports
        print("\n📋 STEP 5: Generating Final Reports")
        print("─" * 100)
        await self._generate_reports()

        # Summary
        print("\n" + "=" * 100)
        print("✅ FULL PIPELINE TEST COMPLETE")
        print("=" * 100 + "\n")

    async def _test_connectivity(self) -> bool:
        """Test Ollama connectivity"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.ollama_base_url}/api/tags",
                    timeout=5.0
                )
                if response.status_code == 200:
                    models = response.json().get('models', [])
                    print(f"✅ Ollama Connected")
                    print(f"   Available Models: {len(models)}")
                    for model in models:
                        print(f"   - {model['name']}")
                    self.results['ollama_connected'] = True
                    return True
                else:
                    print(f"❌ Ollama returned status {response.status_code}")
                    return False
        except Exception as e:
            print(f"❌ Cannot connect to Ollama: {e}")
            return False

    async def _test_content_generation(self) -> List[Dict[str, Any]]:
        """Test content generation with multiple models"""
        tester = OllamaGenerationTester(self.ollama_base_url)

        test_cases = [
            {
                'name': 'Technical Content',
                'model': 'mistral',
                'prompt': 'Write a detailed technical explanation of how REST APIs work, including request/response examples'
            },
            {
                'name': 'Creative Content',
                'model': 'llama2',
                'prompt': 'Write an engaging blog post introduction about the future of artificial intelligence'
            },
            {
                'name': 'Educational Content',
                'model': 'mistral',
                'prompt': 'Explain machine learning concepts to a high school student in simple terms'
            },
        ]

        generation_results = []

        for test_case in test_cases:
            print(f"\n📝 Test: {test_case['name']}")
            print(f"   Model: {test_case['model']}")
            print(f"   Prompt: {test_case['prompt'][:60]}...")

            result = await tester.test_model_generation(
                model=test_case['model'],
                prompt=test_case['prompt'],
                timeout=120
            )

            if result['success']:
                print(f"   ✅ Success")
                print(f"      Quality: {result['quality_score']}/100")
                print(f"      Length: {len(result['response'])} chars")
                print(f"      Time: {result['generation_time']}s")
                print(f"      Tokens/sec: {result['tokens_per_second']}")

                result['test_name'] = test_case['name']
                generation_results.append(result)
                self.results['models_tested'][test_case['model']] = result
            else:
                print(f"   ❌ Failed: {result.get('error')}")

        return generation_results

    async def _assess_content_quality(
        self,
        generation_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Assess quality of generated content"""
        assessor = QualityAssessor()
        quality_results = []

        for result in generation_results:
            print(f"\n🔍 Assessing: {result.get('test_name', 'Unknown')}")

            assessment = await assessor.assess_content(
                content=result['response'],
                context={
                    'topic': 'Content generated by Ollama',
                    'target_audience': 'General audience',
                }
            )

            print(f"   Overall Score: {assessment['overall_score']}/100")
            print(f"   Quality Level: {assessment.get('quality_level', 'Unknown')}")
            print(f"   Pass Check: {'✅ Yes' if assessment.get('pass_quality_check') else '❌ No'}")

            # Print dimension scores
            if 'dimension_scores' in assessment:
                print(f"   Scores:")
                for dim, score in assessment['dimension_scores'].items():
                    print(f"      - {dim}: {score}/100")

            assessment['generation_result'] = result
            quality_results.append(assessment)
            self.results['quality_assessments'].append(assessment)

        return quality_results

    async def _test_backend_integration(self) -> Dict[str, Any]:
        """Test FastAPI backend integration"""
        print("🔗 Testing Backend API...")

        try:
            async with httpx.AsyncClient() as client:
                # Test 1: Health check
                print("\n   1️⃣ Health Check")
                response = await client.get(
                    f"{self.backend_base_url}/api/health",
                    timeout=5.0
                )
                health_ok = response.status_code == 200
                print(f"      {'✅' if health_ok else '❌'} GET /api/health: {response.status_code}")

                # Test 2: Create task
                print("\n   2️⃣ Create Generation Task")
                task_payload = {
                    "task_name": "Ollama E2E Test",
                    "topic": "Benefits of cloud computing",
                    "primary_keyword": "cloud computing",
                    "target_audience": "business decision makers",
                    "metadata": {
                        "model": "mistral",
                        "test_type": "e2e_pipeline"
                    }
                }

                response = await client.post(
                    f"{self.backend_base_url}/api/tasks",
                    json=task_payload,
                    timeout=10.0
                )

                if response.status_code == 201:
                    task_data = response.json()
                    task_id = task_data.get('id')
                    print(f"      ✅ POST /api/tasks: Created task {task_id}")

                    # Test 3: Get task status
                    print("\n   3️⃣ Get Task Status")
                    response = await client.get(
                        f"{self.backend_base_url}/api/tasks/{task_id}",
                        timeout=5.0
                    )

                    if response.status_code == 200:
                        status_data = response.json()
                        print(f"      ✅ GET /api/tasks/{task_id}: Status {status_data.get('status')}")

                        # Test 4: Update task with result
                        print("\n   4️⃣ Update Task with Generation Result")
                        update_payload = {
                            "status": "completed",
                            "result": {
                                "title": "Cloud Computing Benefits",
                                "content": "# Cloud Computing Benefits\n\nCloud computing offers scalability, cost efficiency, and reliability.",
                                "excerpt": "Learn about the key benefits of cloud computing"
                            }
                        }

                        response = await client.patch(
                            f"{self.backend_base_url}/api/tasks/{task_id}",
                            json=update_payload,
                            timeout=5.0
                        )

                        if response.status_code == 200:
                            updated = response.json()
                            print(f"      ✅ PATCH /api/tasks/{task_id}: Status {updated.get('status')}")

                            # Test 5: Publish task
                            print("\n   5️⃣ Publish Task to Database")
                            response = await client.post(
                                f"{self.backend_base_url}/api/tasks/{task_id}/publish",
                                timeout=10.0
                            )

                            if response.status_code == 200:
                                publish_data = response.json()
                                print(f"      ✅ POST /api/tasks/{task_id}/publish: {publish_data.get('message')}")
                                self.results['backend_integration']['publish_success'] = True
                            else:
                                print(f"      ⚠️ POST /api/tasks/{task_id}/publish: {response.status_code}")
                        else:
                            print(f"      ⚠️ PATCH failed: {response.status_code}")
                    else:
                        print(f"      ⚠️ GET failed: {response.status_code}")
                else:
                    print(f"      ⚠️ POST /api/tasks failed: {response.status_code}")

        except Exception as e:
            print(f"   ❌ Backend integration error: {e}")
            self.results['backend_integration']['error'] = str(e)

        return self.results['backend_integration']

    async def _generate_reports(self):
        """Generate comprehensive reports"""
        # Summary statistics
        if self.results['models_tested']:
            quality_scores = [
                a.get('overall_score', 0)
                for a in self.results['quality_assessments']
            ]
            if quality_scores:
                self.results['summary'] = {
                    'total_generations': len(self.results['models_tested']),
                    'avg_quality': round(sum(quality_scores) / len(quality_scores), 1),
                    'highest_quality': max(quality_scores),
                    'lowest_quality': min(quality_scores),
                    'pass_rate': round(
                        sum(1 for a in self.results['quality_assessments'] if a.get('pass_quality_check'))
                        / len(self.results['quality_assessments']) * 100,
                        1
                    )
                }

        # Save results to file
        results_file = Path(__file__).parent / "ollama_e2e_results.json"
        with open(results_file, 'w') as f:
            json.dump(self.results, f, indent=2, default=str)
        print(f"📁 Results saved to: {results_file}")

        # Print summary
        print(f"\n📊 PIPELINE SUMMARY")
        print(f"{'─' * 100}")
        if self.results['summary']:
            for key, value in self.results['summary'].items():
                print(f"   {key}: {value}")

        # Print test results summary
        print(f"\n📈 TEST RESULTS")
        print(f"{'─' * 100}")
        print(f"   Models Tested: {len(self.results['models_tested'])}")
        print(f"   Quality Assessments: {len(self.results['quality_assessments'])}")
        print(f"   Backend Tests: {'✅ Pass' if self.results['backend_integration'].get('publish_success') else '⚠️ Partial'}")


async def main():
    """Main entry point"""
    tester = OllamaPipelineTester()
    await tester.run_full_pipeline()


if __name__ == "__main__":
    asyncio.run(main())

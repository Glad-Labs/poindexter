#!/usr/bin/env node
/**
 * Workflow System Debugging Guide
 * 
 * This script helps verify the workflow system is working correctly
 * by testing each component independently.
 */

const http = require('http');

async function testEndpoint(method, path, description) {
  return new Promise((resolve) => {
    const options = {
      hostname: 'localhost',
      port: 8000,
      path: path,
      method: method,
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: 5000,
    };

    const startTime = Date.now();
    const req = http.request(options, (res) => {
      let data = '';
      
      res.on('data', (chunk) => {
        data += chunk;
      });

      res.on('end', () => {
        const elapsed = Date.now() - startTime;
        console.log(`  ✓ ${description}`);
        console.log(`    Status: ${res.statusCode} | Time: ${elapsed}ms`);
        
        try {
          const parsed = JSON.parse(data);
          if (parsed.phases) {
            console.log(`    Phases: ${parsed.phases.length} found`);
          } else if (parsed.workflows) {
            console.log(`    Workflows: ${parsed.workflows.length} found`);
          } else if (parsed.executions !== undefined) {
            console.log(`    Executions: ${parsed.executions.length} found`);
          }
        } catch (_) {
          // Could not parse JSON, might be error response
          if (data.length > 100) {
            console.log(`    Response: ${data.substring(0, 100)}...`);
          }
        }
        resolve(true);
      });
    });

    req.on('timeout', () => {
      console.log(`  ✗ ${description}`);
      console.log(`    TIMEOUT: Request exceeded 5000ms`);
      req.destroy();
      resolve(false);
    });

    req.on('error', (error) => {
      console.log(`  ✗ ${description}`);
      console.log(`    ERROR: ${error.message}`);
      resolve(false);
    });

    req.end();
  });
}

async function runTests() {
  console.log('╔════════════════════════════════════════════════════════════╗');
  console.log('║         WORKFLOW SYSTEM ENDPOINT VERIFICATION                ║');
  console.log('╚════════════════════════════════════════════════════════════╝\n');

  console.log('Testing Backend Endpoints:\n');

  // Health check
  console.log('1. Health Check');
  await testEndpoint('GET', '/health', 'GET /health');
  console.log();

  // Available phases
  console.log('2. Available Phases');
  await testEndpoint('GET', '/api/workflows/available-phases', 'GET /api/workflows/available-phases');
  console.log();

  // List workflows
  console.log('3. User Workflows');
  await testEndpoint('GET', '/api/workflows/custom?limit=10', 'GET /api/workflows/custom?limit=10');
  console.log();

  // Workflow history
  console.log('4. Workflow Execution History');
  await testEndpoint('GET', '/api/workflows/history?limit=10', 'GET /api/workflows/history?limit=10');
  console.log();

  // Workflow statistics
  console.log('5. Workflow Statistics');
  await testEndpoint('GET', '/api/workflows/statistics', 'GET /api/workflows/statistics');
  console.log();

  console.log('╔════════════════════════════════════════════════════════════╗');
  console.log('║                     CHECK COMPLETE                         ║');
  console.log('╚════════════════════════════════════════════════════════════╝\n');

  console.log('To debug UI issues:');
  console.log('1. Open browser Developer Tools (F12)');
  console.log('2. Go to Console tab');
  console.log('3. Navigate to /services in Oversight Hub');
  console.log('4. Click the "Create Custom Workflow" tab');
  console.log('5. Look for logs starting with:');
  console.log('   - [UnifiedServicesPanel]');
  console.log('   - [workflowBuilderService]');
  console.log('   - [cofounderAgentClient]');
  console.log('\nIf you see timeout errors, the backend API might be slow or unresponsive.');
  console.log('If you see phase data, the backend is working and the UI should render the workflow builder.');
}

runTests().catch(console.error);

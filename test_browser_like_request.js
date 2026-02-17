#!/usr/bin/env node
/**
 * Test simulating React browser fetch behavior
 * This mimics how the cofounderAgentClient.makeRequest() function works
 */

async function testWorkflowAPI() {
  const API_BASE_URL = 'http://localhost:8000';
  const endpoint = '/api/workflows/available-phases';
  const url = `${API_BASE_URL}${endpoint}`;
  
  console.log(`Testing: ${url}`);
  console.log('='.repeat(80));
  
  try {
    const config = {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        // No Authorization header since no token available in dev
      },
      // Use AbortController like React does
      signal: AbortSignal.timeout(30000), // 30 second timeout  
    };
    
    console.log(`Request Headers:`, config.headers);
    console.log('Starting fetch...');
    
    const startTime = Date.now();
    const response = await fetch(url, config);
    const elapsed = Date.now() - startTime;
    
    console.log(`✓ Response received in ${elapsed}ms`);
    console.log(`Status: ${response.status} ${response.statusText}`);
    console.log(`Content-Type: ${response.headers.get('content-type')}`);
    
    if (response.ok) {
      const data = await response.json();
      console.log(`✓ SUCCESS: Parsed JSON response`);
      console.log(`  - Phases count: ${data.phases?.length || 0}`);
      if (data.phases && data.phases.length > 0) {
        console.log(`  - Phase names: ${data.phases.map(p => p.name).join(', ')}`);
      }
      return true;
    } else {
      const text = await response.text();
      console.log(`✗ Error response (${response.status}):`);
      console.log(text.substring(0, 200));
      return false;
    }
    
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log(`✗ TIMEOUT: Request aborted (${error.message})`);
    } else {
      console.log(`✗ ERROR: ${error.name}: ${error.message}`);
    }
    return false;
  }
}

// Run the test
testWorkflowAPI()
  .then(success => {
    process.exit(success ? 0 : 1);
  })
  .catch(err => {
    console.error('Unexpected error:', err);
    process.exit(1);
  });

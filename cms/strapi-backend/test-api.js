/**
 * Test API connectivity and permissions
 */

const fetch = require("node-fetch");

const baseURL = "http://localhost:1337/api";

async function testAPI() {
  try {
    console.log("🔍 Testing API connectivity...\n");

    // Test categories endpoint
    console.log("Testing categories endpoint...");
    const categoriesResponse = await fetch(`${baseURL}/categories`);
    console.log(`Status: ${categoriesResponse.status}`);
    
    if (categoriesResponse.ok) {
      const categoriesData = await categoriesResponse.json();
      console.log(`✅ Categories: Found ${categoriesData.data.length} items`);
    } else {
      const error = await categoriesResponse.text();
      console.log(`❌ Categories: ${error}`);
    }

    // Test authors endpoint
    console.log("\nTesting authors endpoint...");
    const authorsResponse = await fetch(`${baseURL}/authors`);
    console.log(`Status: ${authorsResponse.status}`);
    
    if (authorsResponse.ok) {
      const authorsData = await authorsResponse.json();
      console.log(`✅ Authors: Found ${authorsData.data.length} items`);
    } else {
      const error = await authorsResponse.text();
      console.log(`❌ Authors: ${error}`);
    }

    // Test tags endpoint
    console.log("\nTesting tags endpoint...");
    const tagsResponse = await fetch(`${baseURL}/tags`);
    console.log(`Status: ${tagsResponse.status}`);
    
    if (tagsResponse.ok) {
      const tagsData = await tagsResponse.json();
      console.log(`✅ Tags: Found ${tagsData.data.length} items`);
    } else {
      const error = await tagsResponse.text();
      console.log(`❌ Tags: ${error}`);
    }

    console.log("\n🎯 API test complete!");
    
  } catch (error) {
    console.error("❌ API test failed:", error.message);
  }
}

if (require.main === module) {
  testAPI();
}

module.exports = { testAPI };
const fs = require('fs');
const path = require('path');

const file = 'web/oversight-hub/src/services/cofounderAgentClient.js';

// Read file as binary
const content = fs.readFileSync(file);

// Check for UTF-8 BOM
if (content[0] === 0xef && content[1] === 0xbb && content[2] === 0xbf) {
  console.log('🔧 Found BOM - removing...');
  // Remove BOM
  const cleaned = content.slice(3);
  fs.writeFileSync(file, cleaned, 'utf8');
  console.log('✅ BOM removed successfully!');
} else {
  console.log('✓ No BOM found');
}

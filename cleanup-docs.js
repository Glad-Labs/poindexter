const fs = require('fs');
const path = require('path');

// Root directory violation files (45 files)
const rootFiles = [
  'APPROVAL_WORKFLOW_FIX_DEC_23.md',
  'BACKEND_FRONTEND_AUDIT.md',
  'CODEBASE_TECHNICAL_DEBT_AUDIT.md',
  'CONFIGURATION_UPDATE.md',
  'CONSTRAINT_COMPLIANCE_DISPLAY_EXECUTIVE_SUMMARY.md',
  'COST_DASHBOARD_INTEGRATION_COMPLETE.md',
  'COST_DASHBOARD_READY.md',
  'DEVELOPMENT_GUIDE.md',
  'DOCS_CLEANUP_EXECUTIVE_SUMMARY.md',
  'DOCUMENTATION_CLEANUP_ACTION_PLAN.md',
  'DOCUMENTATION_CLEANUP_SUMMARY.md',
  'ESLINT_CONFIGURATION.md',
  'EXACT_CHANGES_DIFF.md',
  'FRONTEND_KPI_FIX.md',
  'IMAGE_RENDERING_FIXES_SUMMARY.md',
  'IMPLEMENTATION_CHECKLIST.md',
  'IMPLEMENTATION_COMPLETE_DEC22.md',
  'IMPLEMENTATION_LOG_DEC22.md',
  'JUSTFILE_AND_POETRY_COMPLETE.md',
  'JUSTFILE_QUICK_REFERENCE.md',
  'LINTING_FINAL_STATUS.md',
  'MODELSELECTIONPANEL_REFACTOR_SUMMARY.md',
  'PHASE_1_COMPLETION_SUMMARY.md',
  'PHASE_1_DETAILED_IMPLEMENTATION.md',
  'PHASE_1_DOCUMENTATION_INDEX.md',
  'PHASE_1_PROGRESS.md',
  'PHASE_1_QUICK_REFERENCE.md',
  'POETRY_AND_JUSTFILE_SETUP.md',
  'POETRY_WORKFLOW_GUIDE.md',
  'QUICKSTART_CONSTRAINT_COMPLIANCE_TEST.md',
  'QUICK_REFERENCE.md',
  'README_CONSTRAINT_COMPLIANCE_DISPLAY.md',
  'TECHNICAL_DEBT_EXECUTIVE_SUMMARY.md',
  'TECHNICAL_DEBT_IMPLEMENTATION_ROADMAP.md',
  'TECHNICAL_DEBT_QUICK_REFERENCE.md',
  'USING_NEW_FEATURES.md',
  'WARNINGS_FIXED_SUMMARY.md',
  'WARNINGS_RESOLUTION_ROOT_CAUSES.md',
  'WORD_COUNT_IMPLEMENTATION_DESIGN.md',
  'WORD_COUNT_WRITING_STYLE_ANALYSIS.md',
  'WORK_COMPLETE_CONSTRAINT_COMPLIANCE.md',
];

// docs/ directory violation files (14 files)
const docsFiles = [
  'CONSTRAINT_COMPLIANCE_DISPLAY_IMPLEMENTATION_STATUS.md',
  'CONSTRAINT_COMPLIANCE_DISPLAY_INDEX.md',
  'CONSTRAINT_COMPLIANCE_DISPLAY_REFERENCE.md',
  'CONSTRAINT_COMPLIANCE_DISPLAY_TESTING.md',
  'COST_DASHBOARD_INTEGRATION.md',
  'COST_DASHBOARD_QUICK_REFERENCE.md',
  'DOCUMENTATION_INDEX.md',
  'FRONTEND_CONSTRAINT_INTEGRATION_COMPLETE.md',
  'FRONTEND_CONSTRAINT_QUICK_REFERENCE.md',
  'FRONTEND_CONSTRAINT_TESTING_GUIDE.md',
  'IMPLEMENTATION_COMPLETE_CHECKLIST.md',
  'SESSION_DEC_26_CONSTRAINT_DISPLAY_FINALIZATION.md',
  'SESSION_SUMMARY_FRONTEND_INTEGRATION.md',
  'WORD_COUNT_IMPLEMENTATION_COMPLETE.md',
  'WORD_COUNT_QUICK_REFERENCE.md',
];

const base = 'c:\\Users\\mattm\\glad-labs-website';
const archiveDir = path.join(base, 'docs', 'archive-old');

// Create timestamp for archiving
const timestamp = '20251229';

console.log('🗂️  Starting documentation cleanup...\n');

let movedCount = 0;
let notFoundCount = 0;

// Archive root directory files
console.log('📁 Archiving root directory violation files...');
rootFiles.forEach((file) => {
  const srcPath = path.join(base, file);
  const destPath = path.join(archiveDir, `${timestamp}_${file}`);

  if (fs.existsSync(srcPath)) {
    try {
      fs.renameSync(srcPath, destPath);
      console.log(`  ✅ ${file}`);
      movedCount++;
    } catch (err) {
      console.log(`  ❌ Error moving ${file}: ${err.message}`);
    }
  } else {
    console.log(`  ⚠️  Not found: ${file}`);
    notFoundCount++;
  }
});

// Archive docs/ directory files
console.log('\n📁 Archiving docs/ directory violation files...');
docsFiles.forEach((file) => {
  const srcPath = path.join(base, 'docs', file);
  const destPath = path.join(archiveDir, `${timestamp}_${file}`);

  if (fs.existsSync(srcPath)) {
    try {
      fs.renameSync(srcPath, destPath);
      console.log(`  ✅ ${file}`);
      movedCount++;
    } catch (err) {
      console.log(`  ❌ Error moving ${file}: ${err.message}`);
    }
  } else {
    console.log(`  ⚠️  Not found: ${file}`);
    notFoundCount++;
  }
});

console.log(`\n🎉 Documentation cleanup complete!`);
console.log(`   ✅ Archived: ${movedCount} files`);
console.log(`   ⚠️  Not found: ${notFoundCount} files`);
console.log(`\n📋 Next steps:`);
console.log(`   1. Review archived files in docs/archive-old/`);
console.log(`   2. Verify docs/ contains only 8 core files (00-07)`);
console.log(
  `   3. Commit changes with: git commit -m "docs: enforce HIGH-LEVEL ONLY policy - archive ${movedCount} violation files"`
);
console.log(`   4. Delete this script: del cleanup-docs.js`);

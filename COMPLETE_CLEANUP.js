const fs = require('fs');
const path = require('path');

// HIGH-LEVEL ONLY Policy: Keep only architecture docs, archive everything else
// Violation patterns:
// - Session summaries (SESSION_*, PHASE_*, WEEK_*, sprint-related)
// - Implementation guides (IMPLEMENTATION_*, _COMPLETE, _READY, _SUMMARY)
// - Status updates (STATUS, PROGRESS, SUMMARY, REPORT without architectural context)
// - Feature/task tracking (TASK_*, CHECKLIST, PLAN without architecture context)
// - Quick references for features (QUICK_*, _GUIDE without architectural value)
// - Setup/configuration guides (SETUP, CONFIG for specific features)
// - Experiment/analysis notes (ANALYSIS, AUDIT, INVESTIGATION outside reference/)

const root = 'c:\\Users\\mattm\\glad-labs-website';
const archiveDir = path.join(root, 'docs', 'archive-old');
const timestamp = '20251230';

// Violation files to archive (comprehensive scan)
const filesToArchive = [
  // Root directory violations
  path.join(root, 'VS_CODE_TASKS_GUIDE.md'),
  path.join(root, 'VSCODE_TASKS_SETUP_COMPLETE.md'),
  path.join(root, 'VISUAL_TRANSFORMATION_EXECUTIVE_SUMMARY.md'),
  path.join(root, 'VISUAL_TRANSFORMATION_COMPLETE.md'),
  path.join(root, 'VISUAL_DESIGN_DOCUMENTATION_INDEX.md'),
  path.join(root, 'SESSION_SUMMARY_ESLINT_COMPLETE.md'),
  path.join(root, 'SESSION_COMPLETE_VISUAL_TRANSFORMATION.md'),
  path.join(root, 'SESSION_COMPLETE.md'),
  path.join(root, 'QUICK_REFERENCE.md'),
  path.join(root, 'PYTHON_OPTIMIZATION_PLAN.md'),
  path.join(root, 'PYTHON_OPTIMIZATION_GUIDE.md'),
  path.join(root, 'PYTHON_OPTIMIZATION_COMPLETE.md'),
  path.join(root, 'PYTHON_BUILD_FIX_SUMMARY.md'),
  path.join(root, 'PRODUCTION_STATUS_NOVEMBER_5.md'),
  path.join(root, 'PRODUCTION_FIXES_APPLIED.md'),
  path.join(root, 'PRODUCTION_ACTION_PLAN.md'),
  path.join(root, 'POST_MERGE_CHECKLIST.md'),
  path.join(root, 'POSTS_AND_ADSENSE_SETUP.md'),
  path.join(root, 'PHASE_2_COMPLETE.md'),
  path.join(root, 'PHASE_2_5_VERIFICATION.md'),
  path.join(root, 'PHASE_2_5_TROUBLESHOOTING.md'),
  path.join(root, 'PHASE_2_5_STATUS_DASHBOARD.md'),
  path.join(root, 'PHASE_2_5_READY.md'),
  path.join(root, 'PHASE_2_5_EXECUTION_GUIDE.md'),
  path.join(root, 'PHASE_2_5_BRIEF.md'),
  path.join(root, 'PAGE_VERIFICATION_TESTING_GUIDE.md'),
  path.join(root, 'MONOREPO_FIXES_SUMMARY.md'),
  path.join(root, 'MONOREPO_AUDIT_REPORT_NOVEMBER_2025.md'),
  path.join(root, 'MERGE_CONFLICT_RESOLUTION_COMPLETE.md'),
  path.join(root, 'LOCK_FILE_FIX.md'),
  path.join(root, 'INTEGRATION_COMPLETE_SUMMARY.md'),
  path.join(root, 'GITHUB_SECRETS_SETUP.md'),
  path.join(root, 'GITHUB_SECRETS_QUICK_SETUP.md'),
  path.join(root, 'GITHUB_SECRETS_QUICK_REFERENCE.md'),
  path.join(root, 'FIX_PSYCOPG2_DEPLOYMENT.md'),
  path.join(root, 'DOCUMENTATION_REORGANIZATION_COMPLETE.md'),
  path.join(root, 'DOCUMENTATION_INDEX.md'),
  path.join(root, 'DOCUMENTATION_CLEANUP_SUMMARY.md'),
  path.join(root, 'DOCUMENTATION_CLEANUP_ACTION_PLAN.md'),
  path.join(root, 'DOCS_CLEANUP_INSTRUCTIONS_DEC29.md'),
  path.join(root, 'DOCS_CLEANUP_EXECUTIVE_SUMMARY_DEC29.md'),
  path.join(root, 'DOCS_CLEANUP_EXECUTIVE_SUMMARY.md'),
  path.join(root, 'COPILOT_INSTRUCTIONS_UPDATE_SUMMARY.md'),
  path.join(root, 'AUDIT_COMPLETE_READ_ME_FIRST.md'),
  path.join(root, 'DEPLOYMENT_CHECKLIST_FIX.md'),
  // docs/ directory violations
  path.join(root, 'docs', 'VISUAL_DESIGN_QUICK_REFERENCE.md'),
  path.join(root, 'docs', 'VISUAL_DESIGN_COMPLETE.md'),
  path.join(root, 'docs', 'reference', 'CLEANUP_FINAL_SUMMARY.md'),
  path.join(root, 'docs', 'reference', 'CONTENT_SETUP_GUIDE.md'),
  path.join(root, 'docs', 'reference', 'STROBING_FIX.md'),
  path.join(root, 'docs', 'reference', 'SRC_QUICK_REFERENCE_DIAGRAMS.md'),
  path.join(root, 'docs', 'reference', 'SRC_FOLDER_PIPELINE_WALKTHROUGH.md'),
  path.join(root, 'docs', 'reference', 'SRC_DOCUMENTATION_SUMMARY.md'),
  path.join(root, 'docs', 'reference', 'SRC_CODE_EXAMPLES.md'),
  path.join(root, 'docs', 'reference', 'SEED_DATA_GUIDE.md'),
  path.join(root, 'docs', 'reference', 'README_SRC_ARCHITECTURE.md'),
  path.join(
    root,
    'docs',
    'reference',
    'DOCUMENTATION_CLEANUP_EXECUTIVE_SUMMARY.md'
  ),
  path.join(
    root,
    'docs',
    'reference',
    'DOCUMENTATION_CLEANUP_COMPLETION_REPORT.md'
  ),
  path.join(root, 'docs', 'reference', 'DOCUMENTATION_ANALYSIS_FINAL.md'),
  path.join(
    root,
    'docs',
    'reference',
    'GITHUB_SECRETS_COMPLETE_SETUP_GUIDE.md'
  ),
];

let moved = 0;
let notFound = 0;
let errors = [];

console.log('🗂️  Starting comprehensive documentation cleanup...\n');

filesToArchive.forEach((srcPath, idx) => {
  if (!fs.existsSync(srcPath)) {
    notFound++;
    return;
  }

  const baseName = path.basename(srcPath);
  const dstPath = path.join(archiveDir, `${timestamp}_${baseName}`);

  try {
    fs.renameSync(srcPath, dstPath);
    moved++;
    process.stdout.write(
      `\r  ✅ Archived: ${moved}/${filesToArchive.length} files`
    );
  } catch (err) {
    errors.push({ file: baseName, error: err.message });
  }
});

console.log(`\n\n🎉 Cleanup Complete!`);
console.log(`   ✅ Archived: ${moved} files`);
console.log(`   ⚠️  Not found: ${notFound} files`);
if (errors.length > 0) {
  console.log(`   ❌ Errors: ${errors.length} files`);
  errors.forEach((e) => console.log(`      - ${e.file}: ${e.error}`));
}

console.log(`\n📋 Next steps:`);
console.log(`   1. Update docs/00-README.md with new metrics`);
console.log(
  `   2. Commit: git add . && git commit -m "docs: enforce HIGH-LEVEL ONLY policy - archive ${moved} files"`
);
console.log(`   3. Delete: rm COMPLETE_CLEANUP.js`);

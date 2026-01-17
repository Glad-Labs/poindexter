# FILE STRUCTURE: Where Everything Is

**Quick navigation guide**

---

## 📂 ANALYSIS DOCUMENTS (All in Project Root)

```
glad-labs-website/
│
├─ 📖 INDEX_COMPLETE_ANALYSIS.md ⭐ START HERE
│  └─ Master index and navigation guide
│     Read this first (10 min)
│
├─ 🔍 ACTIVE_VS_DEPRECATED_AUDIT.md (40 pages)
│  └─ Deep dive into what's active vs deprecated
│     Read for: Architecture review, code audits
│
├─ 👨‍💻 CONTENT_PIPELINE_DEVELOPER_GUIDE.md (30 pages)
│  └─ How to understand, debug, and modify the pipeline
│     Read for: Development, modifications, debugging
│
├─ ⚡ QUICK_REFERENCE_CARD.md (3 pages)
│  └─ One-page cheat sheet
│     Read for: Quick lookups (PRINT THIS!)
│
├─ 📦 CODE_ANALYSIS_PACKAGE_README.md (10 pages)
│  └─ Overview of the entire package
│     Read for: Understanding what you have
│
├─ ✅ DELIVERY_SUMMARY.md (5 pages)
│  └─ This is where everything came from
│     Read for: Summary of work done
│
└─ 🧹 scripts/cleanup_deprecated_code.py
   └─ Automated cleanup tool
      Run for: Archiving deprecated code
```

---

## 🔧 THE ACTUAL CODEBASE

```
src/cofounder_agent/

├─ 🌟 services/content_router_service.py
│  └─ THE MAIN PIPELINE (6 STAGES)
│     This is what does all the content generation
│     Line: process_content_generation_task() function
│
├─ 🛣️ routes/content_routes.py
│  └─ REST API ENTRY POINT
│     This is what the frontend calls
│     Function: create_content_task()
│
├─ 🤖 agents/
│  ├─ content_agent/
│  │  ├─ core.py (research, create, refine)
│  │  ├─ quality_agent.py (evaluate quality)
│  │  └─ [other files]
│  └─ image_agent/
│     └─ [image search/generation]
│
├─ 💾 services/ (All active)
│  ├─ database_service.py (PostgreSQL)
│  ├─ quality_service.py (Quality evaluation)
│  ├─ image_service.py (Pexels integration)
│  ├─ model_router.py (LLM selection)
│  ├─ unified_orchestrator.py (Task coordination)
│  ├─ cost_calculator.py (Cost estimation)
│  └─ [other services]
│
├─ 🗑️ orchestrator_logic.py (DEPRECATED - 0 imports)
│  └─ OLD code, safe to delete/archive
│
└─ [other files - all active]
```

---

## 📊 DOCUMENT FLOW CHART

```
You're here (reading this file)
        ↓
Read: INDEX_COMPLETE_ANALYSIS.md ⭐
        ↓
    Pick your use case:
    ├─ "I'm new"          → Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md
    ├─ "I'll modify code"  → Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md
    ├─ "I'll review code"  → Read: ACTIVE_VS_DEPRECATED_AUDIT.md
    ├─ "I'll clean up"     → Read: ACTIVE_VS_DEPRECATED_AUDIT.md + Run: cleanup script
    └─ "I need reference"  → Print: QUICK_REFERENCE_CARD.md
```

---

## 🎯 WHAT EACH DOCUMENT IS FOR

| Document                            | Type        | Length | Read Time | Purpose                 |
| ----------------------------------- | ----------- | ------ | --------- | ----------------------- |
| INDEX_COMPLETE_ANALYSIS.md          | Guide       | 8 pg   | 10 min    | Navigate everything     |
| ACTIVE_VS_DEPRECATED_AUDIT.md       | Analysis    | 40 pg  | 45 min    | Understand architecture |
| CONTENT_PIPELINE_DEVELOPER_GUIDE.md | How-to      | 30 pg  | 45 min    | Develop/debug           |
| QUICK_REFERENCE_CARD.md             | Cheat sheet | 3 pg   | 5 min     | Quick lookup            |
| CODE_ANALYSIS_PACKAGE_README.md     | Overview    | 10 pg  | 15 min    | Understand package      |
| DELIVERY_SUMMARY.md                 | Summary     | 5 pg   | 5 min     | What you got            |
| This file                           | Map         | 2 pg   | 2 min     | Where everything is     |

---

## 🚀 QUICK START (5 MINUTES)

### Step 1: Start Here

```
👉 Open: INDEX_COMPLETE_ANALYSIS.md
   Read section: "How to Use This Package"
   Time: 5 minutes
```

### Step 2: Pick Your Path

```
A. "I'm new to the system"
   👉 Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md
      Section: "Quick Start: How Content Gets Generated"
      Time: 5 minutes

B. "I need to modify the pipeline"
   👉 Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md
      Section: "The Actual 6-Stage Pipeline"
      Time: 20 minutes

C. "I need to clean up code"
   👉 Read: ACTIVE_VS_DEPRECATED_AUDIT.md
      Section: "Part 12: Final Recommendation"
      Time: 5 minutes

D. "I need quick reference"
   👉 Print: QUICK_REFERENCE_CARD.md
      Time: 0 minutes (print it!)
```

### Step 3: Start Coding

```
👉 Use: QUICK_REFERENCE_CARD.md to find file locations
   Use: CONTENT_PIPELINE_DEVELOPER_GUIDE.md for examples
   Run: npm run dev to see pipeline in action
```

---

## 📍 FINDING THINGS

### Want to know what's active?

→ ACTIVE_VS_DEPRECATED_AUDIT.md → Part 5: "Active Services"

### Want to understand the pipeline?

→ CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "The 6-Stage Pipeline"

### Want to modify a stage?

→ CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "How to Modify the Pipeline"

### Want to find a file?

→ QUICK_REFERENCE_CARD.md → "File Locations"
OR
→ CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "Architecture Summary"

### Want to debug?

→ CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "Monitoring & Debugging"

### Want to know what's deprecated?

→ ACTIVE_VS_DEPRECATED_AUDIT.md → "Summary Table"

### Want to run cleanup?

→ DELIVERY_SUMMARY.md → "How to Use This Package" → Option 3

---

## 🔑 KEY FILES YOU NEED TO KNOW

### For Content Generation

```
src/cofounder_agent/services/content_router_service.py
└─ This is THE main pipeline
   6 stages: research → draft → quality → refine → image → seo → post → training
   Function: process_content_generation_task()
```

### For REST API

```
src/cofounder_agent/routes/content_routes.py
└─ This handles /api/content/tasks endpoint
   Function: create_content_task()
```

### For Quality Evaluation

```
src/cofounder_agent/services/quality_service.py
└─ This scores content quality (0-10)
   Dimensions: clarity, accuracy, completeness, relevance, seo_quality, readability, engagement
```

### For Debugging

```
Look at logs when running:
npm run dev:cofounder
└─ Watch for emoji markers: 🔍 ✍️ 📋 💡 🖼️ 📊 📝 🎓
```

---

## ✅ VERIFICATION CHECKLIST

Before you start, verify you have all 6 files:

- [ ] INDEX_COMPLETE_ANALYSIS.md (in root)
- [ ] ACTIVE_VS_DEPRECATED_AUDIT.md (in root)
- [ ] CONTENT_PIPELINE_DEVELOPER_GUIDE.md (in root)
- [ ] QUICK_REFERENCE_CARD.md (in root)
- [ ] CODE_ANALYSIS_PACKAGE_README.md (in root)
- [ ] scripts/cleanup_deprecated_code.py (in scripts/ folder)

✅ If you have all 6, you're ready to go!

---

## 🎓 LEARNING PATH

### Complete Beginner (2 hours)

1. Read: QUICK_REFERENCE_CARD.md (5 min)
2. Read: INDEX_COMPLETE_ANALYSIS.md (15 min)
3. Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "Quick Start" (10 min)
4. Run: `npm run dev` and create a blog post (15 min)
5. Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "All 6 Stages" (60 min)

### Experienced Developer (30 minutes)

1. Skim: QUICK_REFERENCE_CARD.md (3 min)
2. Read: ACTIVE_VS_DEPRECATED_AUDIT.md → "Summary Table" (2 min)
3. Read: CONTENT_PIPELINE_DEVELOPER_GUIDE.md → "How to Modify" (15 min)
4. Skim: Code examples as needed (10 min)

### DevOps/Maintenance (15 minutes)

1. Read: ACTIVE_VS_DEPRECATED_AUDIT.md → "Part 12" (5 min)
2. Run: `python scripts/cleanup_deprecated_code.py` (2 min)
3. Run: `npm run test:python` (5 min)
4. Read: Cleanup log (3 min)

---

## 💡 TIPS

- **Print QUICK_REFERENCE_CARD.md** - Keep it at your desk
- **Bookmark INDEX_COMPLETE_ANALYSIS.md** - It's your navigation hub
- **Use Ctrl+F to search** - All documents are searchable
- **Follow the emoji markers** - In logs: 🔍 ✍️ 📋 💡 🖼️ 📊 📝 🎓
- **Test your changes** - Always run `npm run test:python` after modifications
- **Keep docs updated** - As code changes, update the relevant document

---

## 🆘 I CAN'T FIND SOMETHING

### File in active codebase?

→ Check: ACTIVE_VS_DEPRECATED_AUDIT.md → "Summary Table"

### Code example?

→ Check: CONTENT_PIPELINE_DEVELOPER_GUIDE.md → Search the document

### How to do something?

→ Check: INDEX_COMPLETE_ANALYSIS.md → "Use Cases"

### Quick reference?

→ Check: QUICK_REFERENCE_CARD.md → "File Locations"

### Still can't find it?

→ Read: CODE_ANALYSIS_PACKAGE_README.md → "Questions & Answers"

---

## 📞 SUPPORT

| Question           | Answer                     | Document                            |
| ------------------ | -------------------------- | ----------------------------------- |
| What's active?     | Services table with status | ACTIVE_VS_DEPRECATED_AUDIT.md       |
| How does it work?  | 6-stage pipeline explained | CONTENT_PIPELINE_DEVELOPER_GUIDE.md |
| Where's the code?  | File locations listed      | QUICK_REFERENCE_CARD.md             |
| How do I modify?   | Step-by-step guide         | CONTENT_PIPELINE_DEVELOPER_GUIDE.md |
| What's deprecated? | Summary with safety check  | ACTIVE_VS_DEPRECATED_AUDIT.md       |
| How do I cleanup?  | Automated script           | scripts/cleanup_deprecated_code.py  |

---

## 🎯 NEXT STEP

**👉 Open: INDEX_COMPLETE_ANALYSIS.md**

It will guide you through everything else.

---

**You have everything you need to understand, develop, and maintain the Glad Labs system!** 🚀

_Last Updated: December 22, 2025_

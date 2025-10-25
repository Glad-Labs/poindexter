# 🚀 GLAD Labs Quick Reference Card

**Last Updated:** October 25, 2025  
**For:** New and existing team members  
**Purpose:** One-page quick reference for all setup, scripts, and common tasks

---

## ⚡ THE ONE COMMAND YOU NEED

```powershell
# First time setup - Run this and you're done!
.\scripts\setup-dev.ps1

# If you broke something:
.\scripts\setup-dev.ps1 -Clean

# If you need debug info:
.\scripts\setup-dev.ps1 -Verbose
```

**That's it.** This replaces 10+ manual steps.

---

## 📋 What This Command Does

| Phase | What It Does           | Why                                     |
| ----- | ---------------------- | --------------------------------------- |
| 1     | Check prerequisites    | Node.js, npm, git                       |
| 2     | Clean (optional)       | Fixes corrupted node_modules            |
| 3     | Create .env            | Configuration setup                     |
| 4     | Root npm install       | **CRITICAL** - Fixes monorepo           |
| 5     | Install @strapi/strapi | **KEY FIX** - Enables module resolution |
| 6     | Install workspaces     | All sub-projects                        |
| 7     | SQLite drivers         | Database support                        |
| 8     | Verify                 | 4 checks to confirm success             |

**Total time: ~5 minutes**

---

## 🌐 Access Services (After Setup)

```
🌐 Public Site       → http://localhost:3000
📊 Oversight Hub     → http://localhost:3001
🛠️ Strapi Admin      → http://localhost:1337/admin
🧠 Backend API Docs  → http://localhost:8000/docs
```

---

## 📚 Documentation Reference

| Need                       | Read This                                          |
| -------------------------- | -------------------------------------------------- |
| **Setup help**             | `.\scripts\setup-dev.ps1 -?` (built-in help)       |
| **Monorepo understanding** | `docs/MONOREPO_SETUP.md`                           |
| **Script purposes**        | `docs/SCRIPTS_AUDIT_REPORT.md`                     |
| **Troubleshooting**        | `docs/MONOREPO_SETUP.md` → Troubleshooting section |
| **Configuration**          | `.env.example` (well-documented)                   |

---

## 🧪 Common Development Tasks

### Testing APIs

```powershell
# Quick backend validation
.\scripts\quick-test-api.ps1

# Full E2E workflow test
.\scripts\test-e2e-workflow.ps1

# Python tests
npm run test:python
```

### Checking Status

```powershell
# Service status
.\scripts\check-services.ps1

# Full development troubleshoot
.\scripts\dev-troubleshoot.ps1

# Diagnose timeout issues
.\scripts\diagnose-timeout.ps1
```

### Cleanup & Reset

```powershell
# Stop all services
.\scripts\kill-services.ps1

# Full setup from scratch
.\scripts\setup-dev.ps1 -Clean

# Fix Strapi build issues
.\scripts\fix-strapi-build.ps1
```

---

## 🔧 Starting Services Manually

```powershell
# If you need to start services individually:

# Terminal 1: Strapi CMS
cd cms/strapi-v5-backend ; npm run develop

# Terminal 2: Backend AI Co-Founder
cd src/cofounder_agent ; python -m uvicorn main:app --reload

# Terminal 3: Public Site
cd web/public-site ; npm run dev

# Terminal 4: Oversight Hub (optional)
cd web/oversight-hub ; npm start
```

Or use one command to start them all:

```powershell
npm run dev
```

---

## ⚙️ Configuration (.env)

### Quick Checklist

- [ ] `NODE_ENV=development` (for local dev)
- [ ] `DATABASE_CLIENT=sqlite` (for local dev)
- [ ] Pick ONE AI option:
  - [ ] `USE_OLLAMA=true` (FREE - recommended) ← Start here
  - [ ] `OPENAI_API_KEY=sk-...` (PAID)
  - [ ] `ANTHROPIC_API_KEY=sk-ant-...` (PAID)
  - [ ] `GOOGLE_API_KEY=...` (FREE tier available)

See `.env.example` for full details and explanations.

---

## 🤔 FAQ - Quick Answers

**Q: I'm new, where do I start?**  
A: Run `.\scripts\setup-dev.ps1` first, then read `docs/MONOREPO_SETUP.md`

**Q: Something broke, what do I do?**  
A: Run `.\scripts\setup-dev.ps1 -Clean` (full reset)

**Q: Port 3000 is already in use**  
A: Run `.\scripts\kill-services.ps1` to stop all services

**Q: I don't understand why setup is so complicated**  
A: Read `docs/MONOREPO_SETUP.md` → "How npm Workspaces Work" section

**Q: How do I test if backend is working?**  
A: Run `.\scripts\quick-test-api.ps1`

**Q: Which scripts should I use?**  
A: Check `docs/SCRIPTS_AUDIT_REPORT.md` for full list with purposes

**Q: I'm getting "Cannot find module" errors**  
A: Usually fixed by running `.\scripts\setup-dev.ps1 -Clean`

**Q: What AI model should I use locally?**  
A: Use Ollama (free, no API key needed). Set `USE_OLLAMA=true` in `.env`

---

## 🚨 Troubleshooting Quick Links

| Issue               | Solution                                      |
| ------------------- | --------------------------------------------- |
| Module not found    | `setup-dev.ps1 -Clean`                        |
| Port already in use | `kill-services.ps1`                           |
| Strapi won't start  | `fix-strapi-build.ps1`                        |
| API not responding  | `quick-test-api.ps1`                          |
| Need diagnostics    | `dev-troubleshoot.ps1`                        |
| Strapi admin blank  | Restart browser, clear cache                  |
| Still stuck?        | Read `docs/MONOREPO_SETUP.md` Troubleshooting |

---

## 📞 When to Use Each Script

```
For INITIAL SETUP:
└─ ./scripts/setup-dev.ps1

For DAILY DEVELOPMENT:
├─ npm run dev (starts all services)
├─ ./scripts/check-services.ps1 (verify status)
└─ ./scripts/quick-test-api.ps1 (validate backend)

For TESTING:
├─ ./scripts/test-e2e-workflow.ps1 (full pipeline)
├─ ./scripts/quick-test-api.ps1 (API only)
└─ npm run test:python (Python tests)

For CLEANUP:
├─ ./scripts/kill-services.ps1 (stop all)
├─ ./scripts/setup-dev.ps1 -Clean (full reset)
└─ ./scripts/dev-troubleshoot.ps1 (diagnose issues)

For HELP:
├─ ./scripts/setup-dev.ps1 -? (help)
├─ docs/MONOREPO_SETUP.md (understanding)
├─ docs/SCRIPTS_AUDIT_REPORT.md (all scripts)
└─ .env.example (configuration help)
```

---

## 🎯 Getting Help

1. **Check the script built-in help:**

   ```powershell
   .\scripts\setup-dev.ps1 -?
   .\scripts\check-services.ps1 -?
   ```

2. **Read the relevant documentation:**
   - General questions → `docs/MONOREPO_SETUP.md`
   - Script purposes → `docs/SCRIPTS_AUDIT_REPORT.md`
   - Configuration → `.env.example`

3. **Run diagnostics:**

   ```powershell
   .\scripts\dev-troubleshoot.ps1
   .\scripts\quick-test-api.ps1
   ```

4. **Check logs:**
   - Strapi: Check terminal running `npm run develop`
   - Backend: Check terminal running `python -m uvicorn`
   - Frontend: Check browser console (F12)

---

## 🎓 Learning Path

**For New Developers (30 min total):**

1. **5 min:** Run setup

   ```powershell
   .\scripts\setup-dev.ps1
   ```

2. **5 min:** Check it works

   ```powershell
   .\scripts\quick-test-api.ps1
   ```

3. **10 min:** Understand why

   ```
   Read: docs/MONOREPO_SETUP.md → Quick Summary section
   ```

4. **5 min:** Know what scripts do

   ```
   Read: docs/SCRIPTS_AUDIT_REPORT.md → Script categories
   ```

5. **5 min:** Create first content
   ```
   Go to: http://localhost:3001
   Create a task in Oversight Hub
   ```

**Done! You're ready to develop.**

---

## ✅ Verification Checklist

After running setup, verify everything works:

- [ ] `.\scripts\setup-dev.ps1` completed without errors
- [ ] Strapi running: http://localhost:1337/admin
- [ ] Public site running: http://localhost:3000
- [ ] Backend API running: http://localhost:8000/docs
- [ ] Oversight Hub running: http://localhost:3001
- [ ] Can create a task in Oversight Hub
- [ ] API responds to ping: `.\scripts\quick-test-api.ps1`

If any fail, run:

```powershell
.\scripts\dev-troubleshoot.ps1
```

---

## 🚀 You're All Set!

| Task                | Command                             | Time    |
| ------------------- | ----------------------------------- | ------- |
| Setup everything    | `.\scripts\setup-dev.ps1`           | 5 min   |
| Fix something       | `.\scripts\setup-dev.ps1 -Clean`    | 5 min   |
| Understand monorepo | Read `docs/MONOREPO_SETUP.md`       | 15 min  |
| Know all scripts    | Read `docs/SCRIPTS_AUDIT_REPORT.md` | 10 min  |
| Start developing    | `npm run dev`                       | Ongoing |

---

## 📖 One More Thing...

**The single most important file to understand is:**

```
docs/MONOREPO_SETUP.md
```

It explains:

- Why the setup is the way it is
- How npm workspaces actually work
- What happens when things go wrong
- How to troubleshoot almost anything

**Bookmark it. Love it. Reference it often.**

---

**Questions?** This quick reference + built-in help should cover 99% of cases.

**Still stuck?** Post a question with:

```
./scripts/dev-troubleshoot.ps1 (output)
./scripts/check-services.ps1 (output)
Browser console errors (F12)
Relevant log excerpt
```

**Welcome to GLAD Labs! 🚀**

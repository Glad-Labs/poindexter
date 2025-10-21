# 🚨 Security Mitigation - Phase 4: Incident Response Plan

**Date**: October 21, 2025  
**Path**: Path A - Mitigate Now + Upgrade Q1 2026  
**Status**: Implementation Guide

---

## 📋 Purpose

Have a documented response plan if a security incident occurs, enabling:

✅ Rapid detection  
✅ Immediate containment  
✅ Thorough investigation  
✅ Safe recovery  
✅ Post-incident analysis

---

## 🚨 Severity Levels

### Severity 1 - CRITICAL

```
Impact: Active exploitation
Examples:
- Confirmed admin account compromise
- Active database breach in progress
- Malicious code deployed to production
- Data exfiltration detected

Response Time: 15 minutes
Escalation: Immediate (all hands)
```

### Severity 2 - HIGH

```
Impact: Likely compromised but no active exploit confirmed
Examples:
- Repeated failed login attempts (brute force detected)
- Unauthorized data access patterns
- Unusual file uploads
- Rate limiting triggered repeatedly

Response Time: 1 hour
Escalation: Security team + management
```

### Severity 3 - MEDIUM

```
Impact: Potential risk but likely false alarm
Examples:
- Single unusual API call
- One failed login attempt
- Unexpected error spike
- Network timeout

Response Time: Next business day
Escalation: Security team review
```

### Severity 4 - LOW

```
Impact: Informational
Examples:
- New vulnerability published (not affecting us)
- General security update available
- Scheduled monitoring notification

Response Time: Weekly review
Escalation: None
```

---

## 📞 Incident Response Team

### On-Call Structure

```
PRIMARY: Chief Information Security Officer (CISO)
├─ Contact: [CISO phone]
├─ Email: [CISO email]
└─ Escalation: Immediate for Severity 1-2

SECONDARY: Lead DevOps Engineer
├─ Contact: [DevOps phone]
├─ Email: [DevOps email]
└─ Role: Technical implementation

TERTIARY: Engineering Lead
├─ Contact: [Eng Lead phone]
├─ Email: [Eng Lead email]
└─ Role: Technical guidance

MANAGEMENT: VP Engineering
├─ Contact: [VP phone]
├─ Email: [VP email]
└─ Role: Decision authority
```

**Update this list with actual contact info after implementation**

---

## ⏰ Response Procedures

### 1️⃣ DETECTION & ALERT

**How alerts arrive:**

- Railway logs (email/Slack)
- Failed login attempts
- CORS error spike
- Manual security check

**First action (5 minutes):**

```bash
# Pull up the alert
1. Read the alert message carefully
2. Note: timestamp, error type, IP (if available)
3. Determine severity (1-4)
4. If Severity 1-2: CALL on-call person immediately
```

---

### 2️⃣ CONTAINMENT (Severity 1-2 Only)

**If admin account compromised:**

```bash
# IMMEDIATE (< 5 minutes)
1. Disable the compromised admin account in Strapi UI
   - Strapi Dashboard → Settings → Users
   - Click user → Disable

2. Kill all active sessions
   - In database (PostgreSQL on Railway):
     DELETE FROM strapi_core__core_store
     WHERE key LIKE '%jwt%' AND user_id = [compromised_user_id];

3. Notify other admins via phone/Slack
   - "Admin account [name] disabled due to security incident"
   - "Do NOT use shared passwords"
   - "Report any unusual activity immediately"

4. Change all admin passwords
   - Don't send via email
   - Use secure password manager
   - Verbal communication preferred
```

**If database breach suspected:**

```bash
# IMMEDIATE (< 15 minutes)
1. Check data integrity
   - Query count from users, content, etc.
   - Compare with weekly backup counts

2. If breach confirmed:
   - Take Strapi offline (stop container on Railway)
   - Keep database running (logs preserved)
   - Notify management and legal

3. Preserve evidence
   - Download all logs from Railway
   - Screenshot error messages
   - Document timeline
```

**If unauthorized file upload detected:**

```bash
# IMMEDIATE (< 10 minutes)
1. Identify uploaded file
   - Location in file system
   - Uploaded by whom
   - When uploaded

2. Quarantine file
   - Move to separate location
   - Don't execute
   - Analyze with antivirus (if possible)

3. Check for impact
   - Did it get served? (check web logs)
   - Did it execute? (check for backdoors)
   - Was it downloaded? (check access logs)
```

---

### 3️⃣ INVESTIGATION (1-2 hours)

**Gather evidence:**

```bash
# Pull comprehensive logs
1. Access Railway dashboard
2. Download all logs from incident timeframe
3. Save to local secure storage

# Analyze logs
Look for patterns:
├─ Source IP (same IP = targeted attack)
├─ User agents (unusual = bot)
├─ Request patterns (brute force = many requests)
├─ Error messages (gives away system info = vulnerability scanner)
└─ Timing (coordinated = planned attack)

# Check database
SELECT * FROM logs WHERE created_at > [incident_start];
SELECT * FROM admin_history WHERE action = 'login' AND created_at > [incident_start];

# Check file system
- Unexpected files uploaded?
- Modified .env files?
- New scripts added?
- Permission changes?
```

**Document findings:**

```markdown
## Incident Investigation Report

**Incident Date/Time**: [When detected]
**Detection Method**: [Alert type]
**Initial Severity**: [1-4]
**Current Status**: [Contained/Investigating/Resolved]

**Timeline**:

- [Time 1]: First alert received
- [Time 2]: Containment actions taken
- [Time 3]: Investigation began
- [Time 4]: Root cause identified

**Root Cause**: [What happened?]

**Scope**: [How many users/records affected?]

**Evidence**: [Links to logs, screenshots, etc.]

**Response Actions**: [What did we do?]

**Outcome**: [Resolved / Monitoring / Escalated]
```

---

### 4️⃣ RECOVERY (2-4 hours)

**Restore from backup:**

```bash
# Step 1: Assess damage
- How much data corrupted/deleted?
- How long has it been like this?
- Can we recover from backup?

# Step 2: Choose recovery point
# Options (keep weekly backups):
- Today's backup (might have malicious content)
- Yesterday's backup (might be safe)
- Last known good (1 week ago, safest but loses a week of data)

# Step 3: Restore database (with Railway support)
- Stop Strapi service
- Restore PostgreSQL from backup
- Verify data integrity
- Restart Strapi service
- Verify functionality

# Step 4: Verify recovery
- Check admin login works
- Verify content is intact
- Test key API endpoints
- Check no errors in logs
```

**Rebuild if necessary:**

```bash
# If backup restoration fails or too corrupted:
1. Deploy fresh Strapi instance
2. Restore content from backup (manual)
3. Update configurations
4. Test thoroughly
5. Update DNS/load balancer if needed
```

---

### 5️⃣ POST-INCIDENT (24 hours)

**After the incident is resolved:**

```bash
# Step 1: Secure all credentials
- Rotate all admin passwords (again)
- Rotate API tokens
- Rotate database passwords
- Rotate service account keys

# Step 2: Review and patch
- Did the attacker find a vulnerability?
- Can we patch it? (upgrade Strapi? Add WAF?)
- Apply patches if available

# Step 3: Communicate with team
- Brief all engineers
- Update security procedures if needed
- Document lessons learned

# Step 4: Notify customers (if data breached)
- Legal guidance
- Breach notification (if required by law)
- Communication template prepared
```

---

## 📊 Incident Log Template

**Create this for every incident (even false alarms):**

```markdown
# Incident Log - [Date] [Incident #]

## Summary

- **Date/Time**: [When]
- **Duration**: [How long]
- **Severity**: [1-4]
- **Status**: [Resolved/Ongoing]

## Detection

- **Method**: [How detected]
- **Alert**: [What alert triggered]
- **First Response**: [Who responded, when]

## Containment

- **Actions Taken**: [What did we do]
- **Time to Contain**: [How long]
- **Resources Used**: [Who, tools]

## Investigation

- **Root Cause**: [What happened]
- **Scope**: [How many users/records]
- **Evidence**: [Links to logs]

## Resolution

- **Fix Applied**: [What fixed it]
- **Time to Resolve**: [How long total]
- **Verification**: [How we confirmed fixed]

## Prevention

- **Future Prevention**: [What stops this again]
- **Ticket Created**: [Link to follow-up work]
- **Timeline**: [When to implement]

## Lessons Learned

- **What Went Well**: [Good decisions]
- **What Could Improve**: [Better decisions next time]
- **Action Items**: [Changes to make]

---

**Reviewed by**: [Name]  
**Approved by**: [Manager]  
**Date**: [Date]
```

---

## 🛡️ Incident Prevention

### Top Attack Vectors (What We're Defending Against)

1. **Brute Force Admin Login**
   - Current Defense: Strong JWT secrets + hidden path
   - Additional Defense: Rate limiting (Phase 2)
   - Detection: Failed login spike in logs

2. **Stolen Admin Credentials**
   - Current Defense: Hidden admin path (makes it harder to find)
   - Additional Defense: Strong password policy
   - Detection: Login from unexpected IP / unusual time

3. **SQL Injection**
   - Current Defense: Strapi ORM (safe by default)
   - Risk: Lower with Strapi v5 (higher with v5.18 vulnerability)
   - Detection: Unusual database query errors

4. **File Upload Exploitation**
   - Current Defense: File type validation in Strapi
   - Risk: Medium (Strapi has upload vulnerabilities)
   - Detection: Unexpected files, file type mismatches

5. **API Exploitation**
   - Current Defense: CORS restrictions (Phase 2)
   - Risk: Medium (Strapi plugins have vulnerabilities)
   - Detection: Unusual API call patterns, errors

6. **Insider Threat**
   - Current Defense: Admin audit logs
   - Risk: Low (limited admin access)
   - Detection: Unusual user behavior, content changes

---

## ✅ Incident Response Checklist

### Before Incident

- [ ] This document reviewed by entire team
- [ ] Contact list created and tested
- [ ] On-call rotation established
- [ ] Backup/recovery procedures tested
- [ ] Incident response team trained
- [ ] Incident log template prepared

### During Incident

- [ ] Severity level determined (1-4)
- [ ] On-call team notified
- [ ] Logs collected
- [ ] Evidence preserved
- [ ] Containment actions taken
- [ ] Timeline documented
- [ ] Management informed
- [ ] Incident log started

### After Incident

- [ ] Investigation completed
- [ ] Root cause identified
- [ ] Recovery verified
- [ ] All credentials rotated
- [ ] Team debriefing held
- [ ] Incident log completed
- [ ] Lessons learned documented
- [ ] Prevention measures implemented

---

## 📞 Quick Reference

**Severity 1 - CRITICAL**

```
CALL: [CISO phone number]
THEN: Disable compromised account
THEN: Take screenshot of alerts
THEN: Don't shut down anything without permission
```

**Severity 2 - HIGH**

```
EMAIL: security-team@glad-labs.com
THEN: Pull up logs from Railway
THEN: Document what happened
THEN: Wait for team guidance
```

**Severity 3-4 - MEDIUM/LOW**

```
SLACK: Post to #security-alerts channel
THEN: Weekly review during team meeting
THEN: No immediate action needed
```

---

## ⏭️ Next Phase

After Phase 4 is complete:
→ Proceed to **Phase 5: Team Brief & Documentation**

---

**Status**: Ready to implement  
**Estimated Time**: 1 hour setup + training  
**Ongoing**: 0 minutes (reactive only)  
**Difficulty**: Medium  
**Risk**: None (planning document only)

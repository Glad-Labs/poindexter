# GOOGLE ADSENSE: QUICK ACTION CHECKLIST

**Get Approved & Start Earning - 30 Minutes**

---

## 🎯 BEFORE YOU START

- [ ] You own/control the domain (yourdomain.com)
- [ ] You have a Google account
- [ ] You have your AdSense Publisher ID ready (or will get it)

---

## ⚡ STEP 1: UPDATE ads.txt (2 minutes)

**File:** `web/public-site/public/ads.txt`

```
FIND:
google.com, ca-pub-xxxxxxxxxxxxxxxx, DIRECT, f08c47fec0942fa0

REPLACE WITH:
google.com, ca-pub-YOUR-ACTUAL-ID-HERE, DIRECT, f08c47fec0942fa0
```

**Where to find your ID:**

- Not yet submitted? Skip, you'll get it after approval
- Already have AdSense? Go to adsense.google.com → Settings → Account Information

---

## ⚡ STEP 2: SET ENVIRONMENT VARIABLES (2 minutes)

**For Local Testing:** Edit `.env.local`

```
NEXT_PUBLIC_ADSENSE_ID=ca-pub-xxxxxxxxxxxxxxxx
NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX (optional)
```

**For Production (Vercel):**

1. Go to: https://vercel.com/dashboard
2. Select your project: glad-labs-website
3. Settings → Environment Variables
4. Add:
   - Name: `NEXT_PUBLIC_ADSENSE_ID`
   - Value: `ca-pub-YOUR-ID`
5. Add:
   - Name: `NEXT_PUBLIC_GA_ID` (optional)
   - Value: Your Google Analytics ID
6. Deploy

---

## ⚡ STEP 3: TEST LOCALLY (5 minutes)

```bash
# From glad-labs-website root:
cd web/public-site

# Build
npm run build

# Start
npm run start

# Visit: http://localhost:3000

# Verify in browser:
# 1. Open DevTools (F12)
# 2. Go to Console tab
# 3. Should see: "[AdSense] Script loaded successfully"
# 4. Pages load without errors
# 5. Mobile view works (responsive)
```

---

## ⚡ STEP 4: DEPLOY TO PRODUCTION (2 minutes)

```bash
# Commit your changes
git add web/public-site/public/ads.txt
git commit -m "docs: add ads.txt with publisher ID"
git push

# Vercel auto-deploys (watch deployment at vercel.com)
# Should complete in 30-60 seconds
```

---

## ⚡ STEP 5: VERIFY ON PRODUCTION (5 minutes)

```
1. Visit: https://yourdomain.com
2. Open DevTools (F12)
3. Go to Console tab
4. Look for: "[AdSense] Script loaded successfully"
5. Check: https://yourdomain.com/ads.txt
   - Should show your Publisher ID
6. Check mobile: Works on phone/tablet?
```

---

## ⚡ STEP 6: SUBMIT TO GOOGLE ADSENSE (5 minutes)

**Option A: New to AdSense**

```
1. Go: https://www.google.com/adsense/
2. Click: "Sign up now" or "Create account"
3. Sign in with Google
4. Enter your domain: yourdomain.com
5. Complete info form
6. Wait 2-3 weeks for approval
```

**Option B: Already Have AdSense**

```
1. Go: https://www.google.com/adsense/
2. Sign in
3. Your Publisher ID already active
4. Add new site: Settings → Add site
5. Enter domain: yourdomain.com
```

---

## 📋 VERIFICATION CHECKLIST

Before you hit submit, verify:

- [ ] ads.txt file exists at `/web/public-site/public/ads.txt`
- [ ] Publisher ID is NOT "ca-pub-xxxxxxxxxxxxxxxx" (placeholder)
- [ ] `NEXT_PUBLIC_ADSENSE_ID` env var is set
- [ ] Local test: AdSense script loaded successfully
- [ ] Production: https://yourdomain.com loads correctly
- [ ] Production: https://yourdomain.com/ads.txt is accessible
- [ ] Mobile view: Works on phones/tablets
- [ ] Domain: Is active and publicly accessible
- [ ] WHOIS: Domain information is public or using guard service

---

## 🚀 WHAT HAPPENS NEXT

```
Day 1:      You submit to Google
Day 1-21:   Google reviews your site (typically 2-3 weeks)
Day 21+:    Approval email from Google
Day 22+:    Start earning from ads!
```

---

## 💰 MAKE MORE MONEY (After Approval)

Once approved, implement these in `/web/public-site/`:

1. **In-article ads** (highest RPM)
   - Between blog post paragraphs
   - Edit: `app/posts/[slug]/page.tsx`

2. **Sidebar ads** (desktop only)
   - Next to article content
   - Edit: `components/PostLayout.jsx`

3. **Footer ads**
   - Below all content
   - Edit: `components/Footer.jsx`

See: `ADSENSE_IMPLEMENTATION_GUIDE.md` for code examples

---

## ⚠️ COMMON MISTAKES TO AVOID

❌ **Don't:**

- Use placeholder Publisher ID in ads.txt
- Forget environment variables
- Click your own ads (violates policy)
- Place ads too close to content
- Use ads as page decorations
- Incentivize clicks
- Have excessive ads (max 3 above the fold)

✅ **Do:**

- Use your real Publisher ID
- Set env vars on Vercel
- Let organic traffic click ads
- Space ads properly
- Focus on user experience first
- Follow Google policies
- Monitor performance in AdSense dashboard

---

## 📞 NEED HELP?

| Issue                      | Solution                                                       |
| -------------------------- | -------------------------------------------------------------- |
| Can't find Publisher ID    | Check adsense.google.com settings, or wait for approval email  |
| ads.txt not accessible     | Verify path is `/public-site/public/ads.txt`, rebuild & deploy |
| AdSense script not loading | Check browser console, verify `NEXT_PUBLIC_ADSENSE_ID` is set  |
| Site not approved          | Check ADSENSE_READINESS_ANALYSIS.md for policy requirements    |
| Earning $0                 | Takes time, need more traffic, check ad placement              |

---

## ✅ SUMMARY

```
Time to complete: 30 minutes
Difficulty: Easy
Approval chance: 95%+
Earning potential: $200-2,500/month (scale dependent)
```

**You're ready. Let's get this done!** 🚀

---

**Last Updated:** January 16, 2026  
**Status:** Ready for Submission  
**Next Step:** Follow Step 1 above

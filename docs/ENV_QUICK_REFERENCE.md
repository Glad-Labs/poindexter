# Quick Environment Variable Checklist

## ✅ VERIFIED: .env.local Has Everything Needed for Local Development

These critical variables are properly configured:

```env
✅ DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
✅ JWT_SECRET=development-secret-key-change-in-production
✅ API_BASE_URL=http://localhost:8000
✅ NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
✅ COFOUNDER_AGENT_PORT=8000
✅ PUBLIC_SITE_PORT=3000
✅ OVERSIGHT_HUB_PORT=3001
✅ STRAPI_URL=http://localhost:1337
✅ REDIS_URL=redis://localhost:6379
✅ NODE_ENV=development
✅ LOG_LEVEL=DEBUG
✅ ENABLE_ANALYTICS=false
✅ ENABLE_ERROR_REPORTING=false
✅ SKIP_MIGRATION=true
```

**Development Status:** ✅ **READY TO USE**

---

## ⚠️ RECOMMENDED: Add These for Better Functionality

### Frontend API Base URL (React Oversight Hub)

```env
# Add to .env.local
REACT_APP_API_URL=http://localhost:8000
```

### Best Practice: Add Environment Marker

```env
# Add to .env.local for clarity
ENVIRONMENT=development
```

### Strapi CMS (if using content management)

```env
# Already have: STRAPI_URL and STRAPI_API_TOKEN
# Consider adding public versions:
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
NEXT_PUBLIC_STRAPI_API_TOKEN=<same as STRAPI_API_TOKEN>
```

---

## 🔴 REQUIRED ONLY IF: Using These Features

### For LLM beyond Ollama

```env
# Choose at least ONE:
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
```

### For GitHub OAuth

```env
GH_OAUTH_CLIENT_ID=<from GitHub Apps>
GH_OAUTH_CLIENT_SECRET=<from GitHub Apps>
```

### For Error Tracking (Sentry)

```env
SENTRY_DSN=https://<key>@sentry.io/<project>
```

### For Email Publishing

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com
```

### For AWS S3 Media Storage

```env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=your-bucket-name
AWS_S3_REGION=us-east-1
```

### For Cloudinary Media Storage

```env
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
```

---

## 📋 PRODUCTION CHECKLIST

Before deploying to production, verify:

### Database

- [ ] DATABASE_URL points to production PostgreSQL
- [ ] Credentials are strong and unique
- [ ] Connection pooling settings appropriate

### Security

- [ ] JWT_SECRET is changed from development value
- [ ] SECRET_KEY is changed from "your-secret-key-here"
- [ ] ALLOWED_ORIGINS restricted to your domains

### LLM Keys

- [ ] At least ONE API key configured (OpenAI/Anthropic/Google)
- [ ] API keys are from production accounts (not dev)

### Frontend URLs

- [ ] NEXT_PUBLIC_API_BASE_URL points to production API
- [ ] NEXT_PUBLIC_SITE_URL matches your domain
- [ ] REACT_APP_API_URL configured

### Monitoring

- [ ] SENTRY_DSN configured for error tracking
- [ ] SENTRY_ENVIRONMENT=production

### Optional but Recommended

- [ ] PEXELS_API_KEY valid (for stock photos)
- [ ] SMTP configured (if sending emails)
- [ ] Redis/caching properly configured
- [ ] Strapi CMS credentials correct

---

## 🎯 CURRENT STATUS

| Aspect | Status | Action |
| --- | --- | --- |
| **Local Dev** | ✅ Ready | No changes needed |
| **LLM Model** | ✅ Ollama configured | Add cloud keys if needed |
| **Database** | ✅ PostgreSQL ready | Verify connection |
| **Authentication** | ✅ JWT working | Changed after fix |
| **API Communication** | ✅ Endpoints accessible | All ports on localhost |
| **Optional Features** | ⚠️ Unconfigured | Configure as needed |

---

## 📖 For More Details

See: `docs/ENVIRONMENT_VARIABLES_AUDIT.md` for complete list of all 100+ variables and their usage.

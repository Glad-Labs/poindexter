# Strapi Content Setup Guide

## About Page & Privacy Policy Setup

These pages require content to be created in the Strapi admin panel. Follow these steps:

### 1. Access Strapi Admin

```bash
# Make sure Strapi is running
cd cms/strapi-v5-backend
npm run develop
```

Open your browser to: `http://localhost:1337/admin`

### 2. Create About Page Content

1. In the Strapi admin sidebar, click **"About Page"** (under Single Types)
2. Fill in the fields:
   - **Title**: `About Glad Labs`
   - **Content**: Use Markdown format, for example:

```markdown
## About Glad Labs

Glad Labs is revolutionizing the way businesses operate with our AI-powered Co-Founder system.

### Our Mission

To democratize access to intelligent business automation and strategic decision-making through advanced AI technology.

### What We Do

- **AI Co-Founder System**: Intelligent business partner providing strategic insights
- **Autonomous Content Creation**: Multi-agent content generation
- **Business Intelligence**: Real-time analytics and monitoring
- **Agent Orchestration**: Sophisticated workflow management

### Technology Stack

Built on cutting-edge technologies:

- Next.js & React
- Python & FastAPI
- OpenAI, Anthropic, Google AI
- Firebase & Strapi
```

1. (Optional) Fill in **SEO** component:
   - **Meta Title**: `About Glad Labs - AI Business Co-Founder`
   - **Meta Description**: `Learn about Glad Labs and our revolutionary AI-powered business co-founder system`

2. Click **"Publish"** in the top right

### 3. Create Privacy Policy Content

1. In the Strapi admin sidebar, click **"Privacy Policy"** (under Single Types)
2. Fill in the fields:
   - **Title**: `Privacy Policy`
   - **Last Updated**: Today's date
   - **Effective Date**: Your policy effective date
   - **Contact Email**: `privacy@gladlabs.com`
   - **Content**: Use Markdown format, for example:

```markdown
## Privacy Policy

**Last Updated:** October 14, 2025  
**Effective Date:** October 1, 2025

### Introduction

Glad Labs, LLC is committed to protecting your privacy. This Privacy Policy explains how we collect, use, and safeguard your information.

### Information We Collect

- **Personal Information**: Name, email, company details
- **Usage Data**: How you interact with our services
- **Technical Data**: IP address, browser type, device info

### How We Use Your Information

We use collected information to:

- Provide and improve our services
- Communicate with you
- Analyze usage patterns
- Comply with legal obligations

### Data Security

We implement appropriate security measures to protect your personal information.

### Your Rights

You have the right to:

- Access your personal information
- Correct inaccurate data
- Request deletion of your data
- Object to data processing

### Contact Us

**Email:** privacy@gladlabs.com  
**Address:** Glad Labs, LLC

### Changes to This Policy

We may update this policy and will notify you of changes.
```

1. (Optional) Fill in **SEO** component:
   - **Meta Title**: `Privacy Policy | Glad Labs`
   - **Meta Description**: `Glad Labs privacy policy and data protection information`

2. Click **"Publish"** in the top right

### 4. Verify Content on Public Site

Once published, your pages will be available at:

- About: `http://localhost:3000/about`
- Privacy Policy: `http://localhost:3000/privacy-policy`

**Note**: The Next.js site has fallback content that displays if Strapi content is not available, so the pages will never appear completely blank.

## API Endpoints

The public site fetches data from these Strapi endpoints:

- About: `GET /api/about?populate=*`
- Privacy Policy: `GET /api/privacy-policy?populate=*`

You can test these directly in your browser or with curl:

```bash
curl http://localhost:1337/api/about?populate=*
curl http://localhost:1337/api/privacy-policy?populate=*
```

## Troubleshooting

### Pages still show fallback content

1. Check that content is **Published** (not just saved as draft)
2. Verify Strapi is running on port 1337
3. Check browser console for API errors
4. Restart Next.js dev server: `npm run dev` (in `web/public-site/`)

### Content not updating

- Next.js uses Incremental Static Regeneration (ISR) with 60-second cache
- Hard refresh the page (Ctrl+F5) or wait 60 seconds
- Or restart the dev server for immediate updates

### API returns 404

- Single types must be created in Strapi admin before they return data
- Check that the content type is published
- Verify the API endpoint URL matches the content type name

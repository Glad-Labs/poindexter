# GLAD Labs Strapi v5 Backend

Headless CMS backend for GLAD Labs content platform powered by [Strapi v5.27.0](https://strapi.io/).

## ✨ Features

- **Strapi v5.27.0** - Modern headless CMS
- **PostgreSQL** - Production-ready database
- **7 Content Types** - Post, Category, Tag, Author, About, Content-Metric, Privacy-Policy
- **REST API** - Auto-generated endpoints for all content types
- **User Permissions** - Role-based access control
- **Local File Uploads** - Built-in media management
- **SEO Components** - Reusable SEO fields for content

## 📦 Content Types

### Core Content

- **Post** - Blog posts with categories, tags, author, and featured image
- **Category** - Organize posts by category
- **Tag** - Tag-based content organization
- **Author** - Author profiles with bio and avatar

### Metadata

- **About** - About page with team members
- **Content-Metric** - Track views, likes, shares for content
- **Privacy-Policy** - Privacy policy pages

## 🚀 Quick Start (5 minutes)

### Local Development

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Server runs at http://localhost:1337
# Admin panel at http://localhost:1337/admin
```

### Deploy to Railway (Production)

See **[QUICK_START_RAILWAY.md](./QUICK_START_RAILWAY.md)** for 5-minute deployment guide.

Or for detailed setup: **[RAILWAY_CLI_SETUP.md](./RAILWAY_CLI_SETUP.md)**

## � Documentation

| Document                                                 | Purpose                           |
| -------------------------------------------------------- | --------------------------------- |
| [QUICK_START_RAILWAY.md](./QUICK_START_RAILWAY.md)       | 5-minute Railway deployment       |
| [RAILWAY_CLI_SETUP.md](./RAILWAY_CLI_SETUP.md)           | Complete Railway setup guide      |
| [RAILWAY_PROJECT_REVIEW.md](./RAILWAY_PROJECT_REVIEW.md) | Project analysis & best practices |
| [RAILWAY_DEPLOYMENT.md](./RAILWAY_DEPLOYMENT.md)         | Environment configuration         |
| [ADMIN_UI_BUILD_STATUS.md](./ADMIN_UI_BUILD_STATUS.md)   | Admin panel information           |

## 🏗️ Project Structure

```
src/
├── api/                    # Content type APIs
│   ├── post/              # Post content type
│   ├── category/          # Category content type
│   ├── tag/               # Tag content type
│   ├── author/            # Author content type
│   ├── about/             # About page
│   ├── content-metric/    # Metrics tracking
│   └── privacy-policy/    # Privacy policy
├── components/            # Reusable components
│   ├── shared/seo.json    # SEO component
│   └── team/team-member.json
└── extensions/            # Plugin extensions

config/
├── database.js            # Database configuration
├── server.js              # Server settings
├── admin.js               # Admin panel config
├── middlewares.js         # HTTP middleware
└── plugins.js             # Plugin settings
```

## 🔧 Environment Variables

### Required

```
DATABASE_CLIENT=postgres      # Database type (sqlite|postgres|mysql)
HOST=0.0.0.0                  # Server host
PORT=1337                     # Server port
```

### Security (Required for Production)

```
APP_KEYS                      # Session encryption keys
API_TOKEN_SALT                # API token salt
ADMIN_JWT_SECRET              # Admin JWT secret
TRANSFER_TOKEN_SALT           # Transfer token salt
JWT_SECRET                    # JWT secret
```

### Optional

```
DATABASE_URL                  # PostgreSQL connection string
STRAPI_TELEMETRY_DISABLED     # Disable telemetry (true|false)
```

See `.env` and `.env.railway` for examples.

## 📡 API Endpoints

All content types auto-generate REST endpoints:

```
GET    /api/posts                    # List posts
POST   /api/posts                    # Create post
GET    /api/posts/:id                # Get post
PUT    /api/posts/:id                # Update post
DELETE /api/posts/:id                # Delete post

# Same pattern for: /categories, /tags, /authors, /about, etc.
```

## 🗄️ Database

### Local Development

- **SQLite** - `.tmp/data.db` (automatic)
- Persists between restarts
- No setup needed

### Production (Railway)

- **PostgreSQL** 15
- Auto-provisioned by Railway
- Daily automatic backups
- Connection pooling configured

## 🔐 Security

For production deployment:

1. **Generate new security keys**

   ```bash
   node -e "console.log(require('crypto').randomBytes(16).toString('base64'))"
   ```

2. **Set in Railway Variables** (Dashboard)
   - APP_KEYS (generate 4 values)
   - API_TOKEN_SALT
   - ADMIN_JWT_SECRET
   - TRANSFER_TOKEN_SALT
   - JWT_SECRET

3. **Configure CORS** (if needed)
   Edit `config/middlewares.js` to allow frontend domains

## 📊 Technology Stack

| Technology | Version | Purpose        |
| ---------- | ------- | -------------- |
| Node.js    | 18+     | Runtime        |
| Strapi     | 5.27.0  | CMS            |
| PostgreSQL | 15      | Database       |
| SQLite     | 3       | Local dev      |
| Nginx      | -       | Web server     |
| Koa        | 2.x     | HTTP framework |

## 🚢 Deployment

### Railway (Recommended)

- **Cost**: $5-25/month depending on usage
- **Setup**: 5 minutes with Railway CLI
- **Auto-scaling**: Yes
- **Backups**: Automatic daily

See [QUICK_START_RAILWAY.md](./QUICK_START_RAILWAY.md)

### Heroku

- **Cost**: $25-50+/month (standard dynos)
- **Setup**: 10 minutes
- **Auto-scaling**: Yes with paid tier

### Self-hosted

- **Cost**: Variable (VPS)
- **Setup**: 30+ minutes
- **Auto-scaling**: Manual

## 📈 Monitoring & Logs

### Local Development

```bash
npm run dev
```

### Production (Railway)

```bash
railway logs --follow
```

### Monitor Resources

```bash
railway monitor
```

## 🛠️ Development Commands

```bash
# Start development server with hot reload
npm run dev

# Build Strapi for production
npm run build

# Start production server
npm run start

# Access Strapi console
npm run console

# Generate API types
npm run types

# Update Strapi
npm run upgrade
```

## 🤝 Integration with Other Services

### Next.js Frontend

```javascript
const STRAPI_URL =
  process.env.NEXT_PUBLIC_STRAPI_URL || 'http://localhost:1337';

async function getPosts() {
  const res = await fetch(`${STRAPI_URL}/api/posts`);
  return res.json();
}
```

### React App

```javascript
const API_URL = process.env.REACT_APP_STRAPI_URL || 'http://localhost:1337';

useEffect(() => {
  fetch(`${API_URL}/api/posts`)
    .then((res) => res.json())
    .then((data) => setData(data));
}, []);
```

### Python Backend

```python
import requests

STRAPI_URL = os.getenv('STRAPI_URL', 'http://localhost:1337')
response = requests.get(f'{STRAPI_URL}/api/posts')
posts = response.json()
```

## 📝 Notes

- Admin panel requires first-time setup (create admin user)
- File uploads stored in `public/uploads/` (local) or S3 (production)
- Database migrations run automatically on start
- TypeScript support available for custom plugins

## 🔗 Resources

- [Strapi Documentation](https://docs.strapi.io)
- [Railway Documentation](https://docs.railway.app)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [REST API Guide](https://docs.strapi.io/dev-docs/api/rest)

## 💬 Support

For issues:

1. Check logs: `railway logs --follow`
2. Read [RAILWAY_PROJECT_REVIEW.md](./RAILWAY_PROJECT_REVIEW.md)
3. Check [QUICK_START_RAILWAY.md](./QUICK_START_RAILWAY.md) troubleshooting

---

**Made with ❤️ for GLAD Labs** - This command will run Strapi in development mode with the service variables available locally

- Open your browser to `http://127.0.0.1:1337/admin`

## 📝 Notes

- After your app is deployed, visit the `/admin` endpoint to create your admin user.
- If you want to use npm with this project make sure you delete the `yarn.lock` file after you have ran `npm install`

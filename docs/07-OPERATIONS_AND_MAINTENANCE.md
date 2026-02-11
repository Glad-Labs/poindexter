# 07 - Operations & Maintenance

**Last Updated:** February 10, 2026  
**Version:** 1.0.0  
**Status:** ‚úÖ Operational

---

## Ì¥ç Monitoring

### Health Checks
The backend provides a comprehensive health endpoint:
\`GET /health\`

### Logging
Logs are centralized via the \`get_logger\` utility in \`services/logger_config.py\`.
- **Level:** Set \`LOG_LEVEL\` in \`.env.local\`.
- **SQL:** Set \`SQL_DEBUG=true\` to monitor database performance.

---

## Ì≤æ Database Maintenance

### Backups
Use the following scripts for routine maintenance:
- \`scripts/backup-local-postgres.sh\`
- \`scripts/backup-production-db.sh\`

### Migrations
The system uses a custom \`MigrationsService\` (located in \`services/migrations.py\`) to ensure schema parity without the overhead of heavy ORMs.

module.exports = ({ env }) => ({
  auth: {
    secret: env('ADMIN_JWT_SECRET', 'dev-secret-key-change-in-production'),
  },
  apiToken: {
    salt: env('API_TOKEN_SALT', 'dev-api-token-salt-change-in-production'),
  },
  transfer: {
    token: {
      salt: env(
        'TRANSFER_TOKEN_SALT',
        'dev-transfer-token-salt-change-in-production'
      ),
    },
  },
  flags: {
    nps: env.bool('FLAG_NPS', true),
    promoteEE: env.bool('FLAG_PROMOTE_EE', true),
  },
});

export default () => ({
  // Disable content-releases plugin to avoid date-fns-tz dependency issue
  // This plugin pulls in date-fns-tz@2.0.1 which conflicts with workspace date-fns versions
  // You can still publish content immediately, just without the scheduled release feature
  // See: docs/STRAPI_DATE_FNS_NIGHTMARE.md for full explanation
  'content-releases': {
    enabled: false
  }
});

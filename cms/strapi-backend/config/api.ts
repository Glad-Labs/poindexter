/**
 * @file cms/strapi-backend/config/api.ts
 * @description Strapi API configuration.
 * @overview This file configures the behavior of Strapi's REST and GraphQL APIs.
 * It allows for setting default and maximum query limits, which is crucial for
 * performance and preventing abuse.
 */

export default ({
  env,
}: {
  env: (key: string, defaultValue?: any) => any;
}) => ({
  /**
   * @property {object} rest - Configuration for the REST API.
   */
  rest: {
    /**
     * @property {number} defaultLimit - The default number of entries to return in a query.
     * @description Sets the default page size for all REST API endpoints. If a `_limit`
     * parameter is not specified in a request, this value will be used.
     * @default 25
     */
    defaultLimit: 25,
    /**
     * @property {number} maxLimit - The maximum number of entries that can be requested.
     * @description This is a security and performance measure to prevent clients from
     * requesting an excessive number of entries at once, which could overload the server.
     * @default 100
     */
    maxLimit: 100,
    /**
     * @property {boolean} withCount - Whether to include a total count in API responses.
     * @description When true, responses for collections will include pagination details,
     * including the total number of entries available. This is useful for building
     * frontend pagination controls.
     * @default true
     */
    withCount: true,
  },
});

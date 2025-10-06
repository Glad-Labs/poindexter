'use strict';

/**
 * intervention router
 */

module.exports = {
  routes: [
    {
      method: 'POST',
      path: '/intervention',
      handler: 'intervention.send',
      config: {
        auth: false,
      },
    },
  ],
};


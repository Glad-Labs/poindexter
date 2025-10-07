'use strict';

/**
 * A set of functions called "actions" for the `intervention` API.
 */

module.exports = {
  send: async (ctx, next) => {
    const { PubSub } = require('@google-cloud/pubsub');
    const pubsub = new PubSub();

    try {
      const topic = pubsub.topic('agent-interventions');
      const message = {
        json: {
          timestamp: new Date().toISOString(),
          source: 'OversightHub',
          action: 'PAUSE_ALL_AGENTS',
          reason: 'Manual intervention triggered by operator via Strapi.',
        },
      };
      await topic.publishMessage(message);
      ctx.body = { success: true, message: 'Intervention signal sent.' };
    } catch (error) {
      strapi.log.error('Failed to send intervention signal:', error);
      ctx.body = {
        success: false,
        message: 'Error sending intervention signal.',
      };
      ctx.status = 500;
    }
  },
};

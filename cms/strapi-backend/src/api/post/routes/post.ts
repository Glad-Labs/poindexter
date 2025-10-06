/**
 * post router
 *
 * This file uses the Strapi factory to create a default core router for the `post` API,
 * which sets up all the standard RESTful routes.
 */

import { factories } from '@strapi/strapi';

export default factories.createCoreRouter('api::post.post');

/**
 * post controller
 *
 * This file uses the Strapi factory to create a default core controller for the `post` API.
 * It handles all the standard CRUD operations for posts.
 */

import { factories } from '@strapi/strapi'

export default factories.createCoreController('api::post.post');

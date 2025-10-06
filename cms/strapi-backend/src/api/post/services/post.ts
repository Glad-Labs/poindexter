/**
 * post service
 *
 * This file uses the Strapi factory to create a default core service for the `post` API.
 * This service contains the business logic for interacting with the database.
 */

import { factories } from '@strapi/strapi';

export default factories.createCoreService('api::post.post');

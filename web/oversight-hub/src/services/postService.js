/**
 * Post Service — CRUD operations for blog posts.
 *
 * Wraps cofounderAgentClient.makeRequest() for consistent auth/error handling.
 * Canonical source for all post operations in the Oversight Hub.
 */
import { makeRequest } from './cofounderAgentClient';

export async function getPosts(offset = 0, limit = 20, publishedOnly = false) {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  if (publishedOnly) params.set('published_only', 'true');

  const data = await makeRequest(`/api/posts?${params}`, 'GET');
  return data;
}

export async function getPost(postId) {
  return makeRequest(`/api/posts/${postId}`, 'GET');
}

export async function createPost(postData) {
  return makeRequest('/api/posts', 'POST', postData);
}

export async function updatePost(postId, updates) {
  return makeRequest(`/api/posts/${postId}`, 'PATCH', updates);
}

export async function deletePost(postId) {
  return makeRequest(`/api/posts/${postId}`, 'DELETE');
}

export async function publishPost(postId) {
  return updatePost(postId, { status: 'published' });
}

export async function archivePost(postId) {
  return updatePost(postId, { status: 'archived' });
}

export async function schedulePost(postId, publishAt) {
  return updatePost(postId, { status: 'scheduled', published_at: publishAt });
}

/**
 * Search posts using FastAPI full-text search
 * Filters by title, excerpt, and content
 */
export async function searchPosts(query, filters = {}) {
  if (!query || query.trim().length === 0) {
    return [];
  }

  try {
    const baseUrl = process.env.NEXT_PUBLIC_STRAPI_API_URL;
    const token = process.env.NEXT_PUBLIC_STRAPI_API_TOKEN;

    // Use FastAPI endpoint for post search
    const filterParams = new URLSearchParams();

    // Search in title, excerpt
    filterParams.append('filters[$or][0][title][$containsi]', query);
    filterParams.append('filters[$or][1][excerpt][$containsi]', query);

    // Optional: Search in content (full-text search)
    // Note: Requires content field to be searchable in Strapi
    filterParams.append('filters[$or][2][content][$containsi]', query);

    // Category filter if provided
    if (filters.categoryId) {
      filterParams.append('filters[category][id][$eq]', filters.categoryId);
    }

    // Tag filter if provided
    if (filters.tagId) {
      filterParams.append('filters[tags][id][$eq]', filters.tagId);
    }

    // Status filter - only published
    filterParams.append('filters[publishedAt][$notNull]', 'true');

    // Pagination and sorting
    const limit = filters.limit || 10;
    const sort = filters.sort || 'publishedAt:desc';

    filterParams.append('pagination[limit]', limit);
    filterParams.append('sort', sort);

    // Include populated fields
    filterParams.append('populate', 'category,tags,coverImage');

    // Execute search
    const response = await fetch(
      `${baseUrl}/api/posts?${filterParams.toString()}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        next: { revalidate: 60 }, // Cache for 60 seconds
      }
    );

    if (!response.ok) {
      if (process.env.NODE_ENV !== 'production') {
        console.error('Search failed:', response.statusText);
      }
      return [];
    }

    const data = await response.json();

    // Transform Strapi response to consistent format
    return (data.data || []).map(transformPost);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('Error searching posts:', error);
    }
    return [];
  }
}

/**
 * Get trending posts based on engagement (optional)
 * For now, returns most recent posts
 */
export async function getTrendingPosts(limit = 5) {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_STRAPI_API_URL;
    const token = process.env.NEXT_PUBLIC_STRAPI_API_TOKEN;

    const response = await fetch(
      `${baseUrl}/api/posts?` +
        'populate=category,tags,coverImage&' +
        'filters[publishedAt][$notNull]=true&' +
        'sort=-publishedAt&' +
        `pagination[limit]=${limit}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        next: { revalidate: 300 }, // Cache for 5 minutes
      }
    );

    if (!response.ok) {
      if (process.env.NODE_ENV !== 'production') {
        console.error('Failed to fetch trending posts:', response.statusText);
      }
      return [];
    }

    const data = await response.json();
    return (data.data || []).map(transformPost);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('Error fetching trending posts:', error);
    }
    return [];
  }
}

/**
 * Get posts by category with search
 */
export async function getPostsByCategory(
  categorySlug,
  searchQuery = '',
  limit = 10
) {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_STRAPI_API_URL;
    const token = process.env.NEXT_PUBLIC_STRAPI_API_TOKEN;

    const params = new URLSearchParams();
    params.append('filters[category][slug][$eq]', categorySlug);
    params.append('filters[publishedAt][$notNull]', 'true');

    if (searchQuery && searchQuery.trim().length > 0) {
      params.append('filters[$or][0][title][$containsi]', searchQuery);
      params.append('filters[$or][1][excerpt][$containsi]', searchQuery);
    }

    params.append('populate', 'category,tags,coverImage');
    params.append('sort', '-publishedAt');
    params.append('pagination[limit]', limit);

    const response = await fetch(`${baseUrl}/api/posts?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      next: { revalidate: 60 },
    });

    if (!response.ok) return [];
    const data = await response.json();
    return (data.data || []).map(transformPost);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('Error fetching posts by category:', error);
    }
    return [];
  }
}

/**
 * Get posts by tag with search
 */
export async function getPostsByTag(tagSlug, searchQuery = '', limit = 10) {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_STRAPI_API_URL;
    const token = process.env.NEXT_PUBLIC_STRAPI_API_TOKEN;

    const params = new URLSearchParams();
    params.append('filters[tags][slug][$eq]', tagSlug);
    params.append('filters[publishedAt][$notNull]', 'true');

    if (searchQuery && searchQuery.trim().length > 0) {
      params.append('filters[$or][0][title][$containsi]', searchQuery);
      params.append('filters[$or][1][excerpt][$containsi]', searchQuery);
    }

    params.append('populate', 'category,tags,coverImage');
    params.append('sort', '-publishedAt');
    params.append('pagination[limit]', limit);

    const response = await fetch(`${baseUrl}/api/posts?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      next: { revalidate: 60 },
    });

    if (!response.ok) return [];
    const data = await response.json();
    return (data.data || []).map(transformPost);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('Error fetching posts by tag:', error);
    }
    return [];
  }
}

/**
 * Transform Strapi post format to consistent frontend format
 */
function transformPost(post) {
  const { attributes, id } = post;

  return {
    id,
    title: attributes.title || '',
    slug: attributes.slug || '',
    excerpt: attributes.excerpt || '',
    content: attributes.content || '',
    publishedAt: attributes.publishedAt || attributes.date,
    coverImage: attributes.coverImage,
    category: attributes.category?.data?.attributes
      ? {
          id: attributes.category.data.id,
          name: attributes.category.data.attributes.name,
          slug: attributes.category.data.attributes.slug,
        }
      : null,
    tags: attributes.tags?.data
      ? attributes.tags.data.map((tag) => ({
          id: tag.id,
          name: tag.attributes.name,
          slug: tag.attributes.slug,
        }))
      : [],
  };
}

/**
 * Get search suggestions for autocomplete
 * Returns unique titles and excerpts that match query
 */
export async function getSearchSuggestions(query, limit = 8) {
  if (!query || query.trim().length < 2) {
    return [];
  }

  try {
    const results = await searchPosts(query, { limit });
    return results.slice(0, limit).map((post) => ({
      id: post.id,
      title: post.title,
      slug: post.slug,
      type: 'post',
    }));
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('Error getting search suggestions:', error);
    }
    return [];
  }
}

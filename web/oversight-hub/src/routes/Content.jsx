import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { getPosts, updatePost, deletePost } from '../lib/apiClient';
import PostEditor from '../components/modals/PostEditor';
import './Content.css';

function Content() {
  const navigate = useNavigate();
  const [contentItems, setContentItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedTab, setSelectedTab] = useState('all');
  const [editingPost, setEditingPost] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');

  // Fetch posts from API
  useEffect(() => {
    fetchPosts();
  }, []);

  const fetchPosts = async () => {
    try {
      setLoading(true);
      const response = await getPosts(0, 100, false);

      // Handle various response formats from backend
      let posts = [];
      if (Array.isArray(response)) {
        posts = response;
      } else if (response && typeof response === 'object') {
        posts = response.items || response.posts || response.data || [];
      }

      // Ensure it's an array
      setContentItems(Array.isArray(posts) ? posts : []);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch posts:', err);
      setError('Failed to load content. Please try again.');
      setContentItems([]);
    } finally {
      setLoading(false);
    }
  };

  const handleEditPost = (post) => {
    setEditingPost(post);
  };

  const handleCloseEditor = () => {
    setEditingPost(null);
  };

  const handleSavePost = async (updatedPost) => {
    try {
      await updatePost(updatedPost.id, updatedPost);
      await fetchPosts(); // Refresh list
      setEditingPost(null);
      alert('Post updated successfully!');
    } catch (err) {
      console.error('Failed to update post:', err);
      alert('Failed to update post. Please try again.');
    }
  };

  const handleDeletePost = async (postId) => {
    if (!window.confirm('Are you sure you want to delete this post?')) {
      return;
    }
    try {
      await deletePost(postId);
      await fetchPosts(); // Refresh list
      alert('Post deleted successfully!');
    } catch (err) {
      console.error('Failed to delete post:', err);
      alert('Failed to delete post. Please try again.');
    }
  };

  const handleViewPost = (post) => {
    // Open post in new tab on public site
    const publicUrl = post.slug
      ? `http://localhost:3000/posts/${post.slug}`
      : `http://localhost:3000/posts`;
    window.open(publicUrl, '_blank');
  };

  // Filter and search content
  const filteredContent = contentItems.filter((item) => {
    const normalizedStatus = (item.status || '')
      .toLowerCase()
      .replace(/_/g, ' ')
      .trim();

    // Status filter
    const statusMatch =
      selectedTab === 'all' || normalizedStatus === selectedTab;

    // Search filter
    const searchMatch =
      !searchQuery ||
      item.title?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.excerpt?.toLowerCase().includes(searchQuery.toLowerCase());

    return statusMatch && searchMatch;
  });

  // Calculate stats
  const totalPosts = contentItems.length;
  const publishedPosts = contentItems.filter(
    (p) => p.status === 'published'
  ).length;
  const draftPosts = contentItems.filter((p) => p.status === 'draft').length;
  const totalViews = contentItems.reduce(
    (sum, p) => sum + (p.view_count || 0),
    0
  );

  return (
    <div className="content-container">
      {/* Header */}
      <div className="dashboard-header" style={{ marginTop: '40px' }}>
        <h1 className="dashboard-title">Content Library</h1>
        <p className="dashboard-subtitle">
          Manage and organize all your published content
        </p>
      </div>

      {/* Action Buttons */}
      <div className="content-actions">
        <button className="btn btn-primary" onClick={() => navigate('/tasks')}>
          ➕ Create New Content
        </button>
        <button className="btn btn-secondary" onClick={fetchPosts}>
          📤 Refresh Content
        </button>
        <button
          className="btn btn-secondary"
          onClick={() => navigate('/settings')}
        >
          ⚙️ Content Settings
        </button>
      </div>

      {/* Stats Cards */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-icon">📄</div>
          <div className="stat-content">
            <h3 className="stat-value">{totalPosts}</h3>
            <p className="stat-label">Total Content</p>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon">✅</div>
          <div className="stat-content">
            <h3 className="stat-value">{publishedPosts}</h3>
            <p className="stat-label">Published</p>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon">📝</div>
          <div className="stat-content">
            <h3 className="stat-value">{draftPosts}</h3>
            <p className="stat-label">In Draft</p>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon">👁️</div>
          <div className="stat-content">
            <h3 className="stat-value">{totalViews.toLocaleString()}</h3>
            <p className="stat-label">Total Views</p>
          </div>
        </div>
      </div>

      {/* Loading/Error States */}
      {loading && (
        <div className="loading-state">
          <p>Loading content...</p>
        </div>
      )}
      {error && (
        <div className="error-state">
          <p>❌ {error}</p>
          <button onClick={fetchPosts} className="btn btn-secondary">
            Retry
          </button>
        </div>
      )}

      {/* Filters & Tabs */}
      <div className="content-filters">
        <div className="filter-tabs">
          <button
            className={`filter-tab ${selectedTab === 'all' ? 'active' : ''}`}
            onClick={() => setSelectedTab('all')}
          >
            All Items
          </button>
          <button
            className={`filter-tab ${selectedTab === 'published' ? 'active' : ''}`}
            onClick={() => setSelectedTab('published')}
          >
            Published
          </button>
          <button
            className={`filter-tab ${selectedTab === 'draft' ? 'active' : ''}`}
            onClick={() => setSelectedTab('draft')}
          >
            Drafts
          </button>
          <button
            className={`filter-tab ${selectedTab === 'in review' ? 'active' : ''}`}
            onClick={() => setSelectedTab('in review')}
          >
            In Review
          </button>
        </div>
        <div className="search-box">
          <input
            type="text"
            placeholder="🔍 Search content..."
            className="search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {/* Content Table */}
      <div className="content-table">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Type</th>
              <th>Status</th>
              <th>Last Updated</th>
              <th>Author</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!loading && filteredContent.length > 0 ? (
              filteredContent.map((item) => (
                <tr key={item.id}>
                  <td className="content-title">
                    <span className="content-icon">📄</span>
                    {item.title}
                  </td>
                  <td className="content-type">Blog Post</td>
                  <td>
                    <span
                      className={`status-badge status-${(item.status || 'draft')
                        .toLowerCase()
                        .replace(' ', '-')}`}
                    >
                      {item.status || 'draft'}
                    </span>
                  </td>
                  <td className="content-date">
                    {item.updated_at
                      ? new Date(item.updated_at).toLocaleDateString()
                      : 'N/A'}
                  </td>
                  <td className="content-author">
                    {item.author_name || 'AI Co-Founder'}
                  </td>
                  <td className="content-actions">
                    <button
                      className="action-btn"
                      title="Edit"
                      onClick={() => handleEditPost(item)}
                    >
                      ✏️
                    </button>
                    <button
                      className="action-btn"
                      title="View"
                      onClick={() => handleViewPost(item)}
                    >
                      👁️
                    </button>
                    <button
                      className="action-btn"
                      title="Delete"
                      onClick={() => handleDeletePost(item.id)}
                    >
                      🗑️
                    </button>
                  </td>
                </tr>
              ))
            ) : !loading ? (
              <tr>
                <td colSpan="6" className="empty-state">
                  {searchQuery
                    ? 'No content matches your search.'
                    : 'No content found. Publish blog posts from the Tasks page!'}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {/* Publishing Schedule */}
      <div className="publishing-schedule">
        <h2 className="section-title">📅 Publishing Schedule</h2>
        <div className="schedule-grid">
          <div className="schedule-item">
            <div className="schedule-date">Oct 25</div>
            <p className="schedule-content">Feature Release Announcement</p>
          </div>
          <div className="schedule-item">
            <div className="schedule-date">Oct 28</div>
            <p className="schedule-content">Monthly Newsletter</p>
          </div>
          <div className="schedule-item">
            <div className="schedule-date">Nov 1</div>
            <p className="schedule-content">Q4 Metrics Report</p>
          </div>
        </div>
      </div>

      {/* Categories */}
      <div className="content-categories">
        <h2 className="section-title">📚 Content Categories</h2>
        <div className="category-grid">
          {[
            'Blog Posts',
            'Documentation',
            'Case Studies',
            'Whitepapers',
            'Videos',
            'Webinars',
          ].map((category, idx) => (
            <div key={idx} className="category-card">
              <span className="category-emoji">📁</span>
              <h3 className="category-name">{category}</h3>
              <p className="category-count">
                {category === 'Blog Posts' ? publishedPosts : 0} items
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Post Editor Modal */}
      {editingPost && (
        <PostEditor
          post={editingPost}
          onClose={handleCloseEditor}
          onSave={handleSavePost}
        />
      )}
    </div>
  );
}

export default Content;

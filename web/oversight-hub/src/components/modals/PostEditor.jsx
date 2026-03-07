import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import './PostEditor.css';

/**
 * PostEditor Modal - Edit published blog posts
 *
 * Features:
 * - Edit title, content, excerpt
 * - Update SEO metadata (title, description, keywords)
 * - Change featured image URL
 * - Markdown content editing
 * - Real-time preview toggle
 */
function PostEditor({ post, onClose, onSave }) {
  const [formData, setFormData] = useState({
    title: '',
    slug: '',
    content: '',
    excerpt: '',
    featured_image_url: '',
    seo_title: '',
    seo_description: '',
    seo_keywords: '',
    status: 'published',
  });

  const [showPreview, setShowPreview] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (post) {
      setFormData({
        title: post.title || '',
        slug: post.slug || '',
        content: post.content || '',
        excerpt: post.excerpt || '',
        featured_image_url: post.featured_image_url || '',
        seo_title: post.seo_title || post.title || '',
        seo_description: post.seo_description || post.excerpt || '',
        seo_keywords: post.seo_keywords || '',
        status: post.status || 'published',
      });
    }
  }, [post]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSave({ ...post, ...formData });
    } catch (error) {
      logger.error('Save failed:', error);
      alert('Failed to save changes. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    if (window.confirm('Discard unsaved changes?')) {
      onClose();
    }
  };

  // Convert markdown to basic HTML for preview
  const renderPreview = (markdown) => {
    if (!markdown) return '<p>No content</p>';

    // Basic markdown conversion
    const html = markdown
      .replace(/^### (.*$)/gim, '<h3>$1</h3>')
      .replace(/^## (.*$)/gim, '<h2>$1</h2>')
      .replace(/^# (.*$)/gim, '<h1>$1</h1>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br/>');

    return `<p>${html}</p>`;
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-container post-editor-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="modal-header">
          <h2>📝 Edit Post</h2>
          <button className="close-btn" onClick={onClose}>
            ×
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="post-editor-form">
          <div className="modal-body">
            {/* Title */}
            <div className="form-group">
              <label htmlFor="title">Title *</label>
              <input
                type="text"
                id="title"
                name="title"
                value={formData.title}
                onChange={handleChange}
                placeholder="Enter post title"
                required
              />
            </div>

            {/* Slug */}
            <div className="form-group">
              <label htmlFor="slug">Slug (URL)</label>
              <input
                type="text"
                id="slug"
                name="slug"
                value={formData.slug}
                onChange={handleChange}
                placeholder="post-url-slug"
                disabled
              />
              <small className="form-hint">
                Slug cannot be changed after publishing
              </small>
            </div>

            {/* Featured Image */}
            <div className="form-group">
              <label htmlFor="featured_image_url">Featured Image URL</label>
              <input
                type="url"
                id="featured_image_url"
                name="featured_image_url"
                value={formData.featured_image_url}
                onChange={handleChange}
                placeholder="https://example.com/image.jpg"
              />
              {formData.featured_image_url && (
                <div className="image-preview">
                  <img src={formData.featured_image_url} alt="Preview" />
                </div>
              )}
            </div>

            {/* Excerpt */}
            <div className="form-group">
              <label htmlFor="excerpt">Excerpt</label>
              <textarea
                id="excerpt"
                name="excerpt"
                value={formData.excerpt}
                onChange={handleChange}
                placeholder="Brief description (shown in post listings)"
                rows="3"
              />
            </div>

            {/* Content Editor */}
            <div className="form-group">
              <div className="editor-header">
                <label htmlFor="content">Content (Markdown) *</label>
                <button
                  type="button"
                  className="toggle-preview-btn"
                  onClick={() => setShowPreview(!showPreview)}
                >
                  {showPreview ? '📝 Edit' : '👁️ Preview'}
                </button>
              </div>

              {!showPreview ? (
                <textarea
                  id="content"
                  name="content"
                  value={formData.content}
                  onChange={handleChange}
                  placeholder="Write your content in Markdown..."
                  rows="15"
                  required
                  className="content-editor"
                />
              ) : (
                <div
                  className="content-preview"
                  dangerouslySetInnerHTML={{
                    __html: renderPreview(formData.content),
                  }}
                />
              )}
            </div>

            {/* SEO Section */}
            <div className="seo-section">
              <h3 className="section-title">🔍 SEO Settings</h3>

              <div className="form-group">
                <label htmlFor="seo_title">SEO Title</label>
                <input
                  type="text"
                  id="seo_title"
                  name="seo_title"
                  value={formData.seo_title}
                  onChange={handleChange}
                  placeholder="SEO-optimized title for search engines"
                  maxLength="60"
                />
                <small className="form-hint">
                  {formData.seo_title.length}/60 characters
                </small>
              </div>

              <div className="form-group">
                <label htmlFor="seo_description">SEO Description</label>
                <textarea
                  id="seo_description"
                  name="seo_description"
                  value={formData.seo_description}
                  onChange={handleChange}
                  placeholder="Meta description for search results"
                  rows="3"
                  maxLength="160"
                />
                <small className="form-hint">
                  {formData.seo_description.length}/160 characters
                </small>
              </div>

              <div className="form-group">
                <label htmlFor="seo_keywords">
                  SEO Keywords (comma-separated)
                </label>
                <input
                  type="text"
                  id="seo_keywords"
                  name="seo_keywords"
                  value={formData.seo_keywords}
                  onChange={handleChange}
                  placeholder="keyword1, keyword2, keyword3"
                />
              </div>
            </div>

            {/* Status */}
            <div className="form-group">
              <label htmlFor="status">Status</label>
              <select
                id="status"
                name="status"
                value={formData.status}
                onChange={handleChange}
              >
                <option value="draft">Draft</option>
                <option value="published">Published</option>
                <option value="archived">Archived</option>
              </select>
            </div>
          </div>

          {/* Footer Actions */}
          <div className="modal-footer">
            <button
              type="button"
              onClick={handleCancel}
              className="btn btn-secondary"
              disabled={saving}
            >
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : '💾 Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default PostEditor;

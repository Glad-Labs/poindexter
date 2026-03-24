import React, { useState, useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';
import './PostEditor.css';
import { logError } from '../../services/errorLoggingService';

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
    published_at: '',
  });

  const [editorMode, setEditorMode] = useState('edit'); // 'edit' | 'preview' | 'split'
  const [showPreview, setShowPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef(null);
  const textareaRef = useRef(null);

  // Insert markdown formatting at cursor position
  const insertMarkdown = (before, after = '') => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const text = formData.content;
    const selected = text.substring(start, end);
    const newText =
      text.substring(0, start) +
      before +
      selected +
      after +
      text.substring(end);
    setFormData((prev) => ({ ...prev, content: newText }));
    // Restore cursor position after state update
    setTimeout(() => {
      textarea.focus();
      const cursorPos = start + before.length + selected.length + after.length;
      textarea.setSelectionRange(cursorPos, cursorPos);
    }, 0);
  };

  // Focus trap and Escape key handler
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    // Move focus into the dialog on mount
    dialog.focus();

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }

      // Focus trap: cycle focus within the dialog
      if (e.key === 'Tab') {
        const focusable = dialog.querySelectorAll(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]):not([disabled])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    dialog.addEventListener('keydown', handleKeyDown);
    return () => dialog.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

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
        published_at: post.published_at
          ? (() => {
              const d = new Date(post.published_at);
              return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            })()
          : '',
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
      const payload = { ...post, ...formData };
      // Convert local datetime-local value to UTC ISO string for the backend
      if (payload.published_at && payload.status === 'scheduled') {
        payload.published_at = new Date(payload.published_at).toISOString();
      }
      await onSave(payload);
    } catch (error) {
      logError(error, {
        severity: 'warning',
        customContext: { component: 'PostEditor', action: 'save' },
      });
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

    // Enhanced markdown conversion
    const html = markdown
      // Code blocks (fenced)
      .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
      // Inline code
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Headings
      .replace(/^#### (.*$)/gim, '<h4>$1</h4>')
      .replace(/^### (.*$)/gim, '<h3>$1</h3>')
      .replace(/^## (.*$)/gim, '<h2>$1</h2>')
      .replace(/^# (.*$)/gim, '<h1>$1</h1>')
      // Bold and italic
      .replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      // Links and images
      .replace(
        /!\[([^\]]*)\]\(([^)]+)\)/g,
        '<img src="$2" alt="$1" style="max-width:100%" />'
      )
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')
      // Blockquotes
      .replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>')
      // Unordered lists
      .replace(/^[\-\*] (.*$)/gim, '<li>$1</li>')
      // Horizontal rule
      .replace(/^---$/gim, '<hr/>')
      // Paragraphs
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br/>');

    // Explicit allowlist narrows the attack surface vs the default blocklist.
    // AI-generated content may contain prompt-injected HTML — blocklist-only sanitization
    // is insufficient when the attacker controls content that flows through the LLM pipeline.
    return DOMPurify.sanitize(`<p>${html}</p>`, {
      ALLOWED_TAGS: [
        'p',
        'b',
        'i',
        'em',
        'strong',
        'a',
        'ul',
        'ol',
        'li',
        'code',
        'pre',
        'blockquote',
        'h1',
        'h2',
        'h3',
        'h4',
        'br',
        'hr',
        'img',
      ],
      ALLOWED_ATTR: ['href', 'title', 'src', 'alt', 'style'],
      FORCE_BODY: true,
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="post-editor-title"
        tabIndex={-1}
        className="modal-container post-editor-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="modal-header">
          <h2 id="post-editor-title">Edit Post</h2>
          <button
            className="close-btn"
            onClick={onClose}
            aria-label="Close dialog"
          >
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
                <div className="editor-mode-buttons">
                  <button
                    type="button"
                    className={`mode-btn ${editorMode === 'edit' ? 'active' : ''}`}
                    onClick={() => setEditorMode('edit')}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className={`mode-btn ${editorMode === 'split' ? 'active' : ''}`}
                    onClick={() => setEditorMode('split')}
                  >
                    Split
                  </button>
                  <button
                    type="button"
                    className={`mode-btn ${editorMode === 'preview' ? 'active' : ''}`}
                    onClick={() => setEditorMode('preview')}
                  >
                    Preview
                  </button>
                </div>
              </div>

              {/* Markdown Toolbar */}
              {editorMode !== 'preview' && (
                <div className="markdown-toolbar">
                  <button
                    type="button"
                    title="Bold"
                    onClick={() => insertMarkdown('**', '**')}
                  >
                    B
                  </button>
                  <button
                    type="button"
                    title="Italic"
                    onClick={() => insertMarkdown('*', '*')}
                  >
                    <em>I</em>
                  </button>
                  <button
                    type="button"
                    title="Heading 2"
                    onClick={() => insertMarkdown('\n## ')}
                  >
                    H2
                  </button>
                  <button
                    type="button"
                    title="Heading 3"
                    onClick={() => insertMarkdown('\n### ')}
                  >
                    H3
                  </button>
                  <button
                    type="button"
                    title="Link"
                    onClick={() => insertMarkdown('[', '](url)')}
                  >
                    Link
                  </button>
                  <button
                    type="button"
                    title="Image"
                    onClick={() => insertMarkdown('![alt](', ')')}
                  >
                    Img
                  </button>
                  <button
                    type="button"
                    title="Bullet List"
                    onClick={() => insertMarkdown('\n- ')}
                  >
                    List
                  </button>
                  <button
                    type="button"
                    title="Code"
                    onClick={() => insertMarkdown('`', '`')}
                  >
                    Code
                  </button>
                  <button
                    type="button"
                    title="Blockquote"
                    onClick={() => insertMarkdown('\n> ')}
                  >
                    Quote
                  </button>
                </div>
              )}

              <div className={`editor-panes ${editorMode}`}>
                {editorMode !== 'preview' && (
                  <textarea
                    ref={textareaRef}
                    id="content"
                    name="content"
                    value={formData.content}
                    onChange={handleChange}
                    placeholder="Write your content in Markdown..."
                    rows="15"
                    required
                    className="content-editor"
                  />
                )}
                {editorMode !== 'edit' && (
                  <div
                    className="content-preview"
                    dangerouslySetInnerHTML={{
                      __html: renderPreview(formData.content),
                    }}
                  />
                )}
              </div>
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
                <option value="scheduled">Scheduled</option>
                <option value="published">Published</option>
                <option value="archived">Archived</option>
              </select>
            </div>

            {/* Schedule Date (shown when status is 'scheduled') */}
            {formData.status === 'scheduled' && (
              <div className="form-group">
                <label htmlFor="published_at">Publish At</label>
                <input
                  type="datetime-local"
                  id="published_at"
                  name="published_at"
                  value={formData.published_at}
                  onChange={handleChange}
                  min={(() => {
                    const d = new Date();
                    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
                  })()}
                  required
                />
              </div>
            )}
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

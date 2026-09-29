/*
 * Poindexter local-site viewer.
 *
 * Reads the static JSON export that storage_provider=local writes to the local
 * folder (static/posts/index.json, static/posts/<slug>.json,
 * static/manifest.json) and renders it. No build step, no dependencies.
 *
 * Routes, relative to the mount (normally /site/):
 *   ""               the post list
 *   "posts/<slug>"   one post
 *
 * Post bodies are HTML written by the pipeline. The page's Content-Security-
 * Policy already stops injected markup from running script; sanitize() removes
 * active content as well, so a post can't do anything a static page couldn't.
 */
(function () {
  'use strict';

  var BASE = new URL('../', document.currentScript.src);
  var app = document.getElementById('app');
  var siteNameLink = document.getElementById('site-name');

  function currentRoute() {
    var rel = location.pathname.slice(BASE.pathname.length);
    var match = rel.match(/^posts\/([^/]+)\/?$/);
    if (match) {
      return { page: 'post', slug: decodeURIComponent(match[1]) };
    }
    return { page: 'index' };
  }

  function getJSON(path) {
    return fetch(new URL(path, BASE), { cache: 'no-store' }).then(
      function (res) {
        if (res.status === 404) {
          return null;
        }
        if (!res.ok) {
          throw new Error(path + ': HTTP ' + res.status);
        }
        return res.json();
      }
    );
  }

  // Build an element whose text is set with textContent, never innerHTML.
  function el(tag, attrs) {
    var node = document.createElement(tag);
    var key;
    attrs = attrs || {};
    for (key in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, key)) {
        continue;
      }
      if (key === 'text') {
        node.textContent = attrs[key];
      } else if (key === 'className') {
        node.className = attrs[key];
      } else {
        node.setAttribute(key, attrs[key]);
      }
    }
    for (var i = 2; i < arguments.length; i += 1) {
      if (arguments[i]) {
        node.appendChild(arguments[i]);
      }
    }
    return node;
  }

  function formatDate(iso) {
    if (!iso) {
      return '';
    }
    var date = new Date(iso);
    if (isNaN(date.getTime())) {
      return '';
    }
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  function postHref(slug) {
    return new URL('posts/' + encodeURIComponent(slug), BASE).pathname;
  }

  var BLOCKED_TAGS =
    'script,iframe,frame,object,embed,form,input,button,style,link,meta,base,template';
  var URL_ATTRS = {
    href: true,
    src: true,
    srcset: true,
    action: true,
    formaction: true,
    poster: true,
    'xlink:href': true,
  };

  // Parse post HTML off-document and return a fragment with active content removed.
  function sanitize(html) {
    var doc = new DOMParser().parseFromString(html || '', 'text/html');
    var blocked = doc.querySelectorAll(BLOCKED_TAGS);
    var i;
    for (i = 0; i < blocked.length; i += 1) {
      blocked[i].remove();
    }
    var all = doc.body.querySelectorAll('*');
    for (i = 0; i < all.length; i += 1) {
      var node = all[i];
      var attrs = Array.prototype.slice.call(node.attributes);
      for (var j = 0; j < attrs.length; j += 1) {
        var name = attrs[j].name.toLowerCase();
        var value = attrs[j].value.replace(/\s+/g, '').toLowerCase();
        if (name.indexOf('on') === 0) {
          node.removeAttribute(attrs[j].name);
        } else if (
          URL_ATTRS[name] &&
          /^(javascript|vbscript|data:text\/html)/.test(value)
        ) {
          node.removeAttribute(attrs[j].name);
        }
      }
      if (
        node.tagName === 'A' &&
        node.getAttribute('href') &&
        /^https?:/i.test(node.getAttribute('href'))
      ) {
        node.setAttribute('rel', 'noopener noreferrer');
      }
    }
    // The page's title is the one <h1>; demote any the writer put in the body,
    // as the public site does.
    var h1s = doc.body.querySelectorAll('h1');
    for (i = 0; i < h1s.length; i += 1) {
      var h2 = doc.createElement('h2');
      while (h1s[i].firstChild) {
        h2.appendChild(h1s[i].firstChild);
      }
      h1s[i].replaceWith(h2);
    }
    var fragment = document.createDocumentFragment();
    while (doc.body.firstChild) {
      fragment.appendChild(document.adoptNode(doc.body.firstChild));
    }
    return fragment;
  }

  function applySiteName(manifest) {
    var name = manifest && manifest.site_name;
    if (name) {
      siteNameLink.textContent = name;
    }
    return name || 'Poindexter';
  }

  function show(node) {
    app.replaceChildren(node);
  }

  function emptyState() {
    return el(
      'section',
      { className: 'notice' },
      el('h1', { text: 'Nothing published yet' }),
      el('p', {
        text: 'Published posts appear here. Drafts wait for review first:',
      }),
      el(
        'pre',
        {},
        el('code', {
          text:
            'poindexter tasks list --status awaiting_approval\n' +
            'poindexter tasks approve <id>\n' +
            'poindexter tasks publish <id>',
        })
      ),
      el('p', {
        className: 'muted',
        text: 'Reload this page after publishing.',
      })
    );
  }

  function postCard(post) {
    var image = null;
    if (post.featured_image_url) {
      image = el('img', {
        className: 'card-image',
        src: post.featured_image_url,
        alt: post.featured_image_alt || '',
        loading: 'lazy',
      });
    }
    var tags = null;
    if (post.tags && post.tags.length) {
      tags = el('ul', { className: 'tags', 'aria-label': 'Tags' });
      post.tags.forEach(function (tag) {
        tags.appendChild(el('li', { text: tag }));
      });
    }
    return el(
      'article',
      { className: 'card' },
      image,
      el(
        'div',
        { className: 'card-body' },
        el(
          'h2',
          {},
          el('a', { href: postHref(post.slug), text: post.title || post.slug })
        ),
        el('p', { className: 'meta', text: formatDate(post.published_at) }),
        post.excerpt
          ? el('p', { className: 'excerpt', text: post.excerpt })
          : null,
        tags
      )
    );
  }

  function renderIndex() {
    return Promise.all([
      getJSON('static/posts/index.json'),
      getJSON('static/manifest.json'),
    ]).then(function (results) {
      var index = results[0];
      var siteName = applySiteName(results[1]);
      document.title = siteName;
      var posts = (index && index.posts) || [];
      if (!posts.length) {
        show(emptyState());
        return;
      }
      var list = el('div', { className: 'cards' });
      posts.forEach(function (post) {
        list.appendChild(postCard(post));
      });
      var count =
        posts.length === 1
          ? '1 published post'
          : posts.length + ' published posts';
      show(
        el(
          'section',
          {},
          el('h1', { className: 'visually-hidden', text: siteName }),
          el('p', { className: 'muted', text: count }),
          list
        )
      );
    });
  }

  function renderPost(slug) {
    return Promise.all([
      getJSON('static/posts/' + encodeURIComponent(slug) + '.json'),
      getJSON('static/manifest.json'),
    ]).then(function (results) {
      var post = results[0];
      var siteName = applySiteName(results[1]);
      var back = el(
        'p',
        { className: 'back' },
        el('a', { href: BASE.pathname, text: '← All posts' })
      );
      if (!post) {
        document.title = 'Not found · ' + siteName;
        show(
          el(
            'section',
            { className: 'notice' },
            back,
            el('h1', { text: 'No published post here' }),
            el('p', {
              text: 'It may not be published yet, or it was taken down.',
            })
          )
        );
        return;
      }
      document.title = (post.title || slug) + ' · ' + siteName;
      var hero = null;
      if (post.featured_image_url) {
        hero = el('img', {
          className: 'hero',
          src: post.featured_image_url,
          alt: post.featured_image_alt || '',
        });
      }
      var body = el('div', { className: 'post-body' });
      body.appendChild(sanitize(post.content));
      show(
        el(
          'article',
          { className: 'post' },
          back,
          el('h1', { text: post.title || slug }),
          el('p', { className: 'meta', text: formatDate(post.published_at) }),
          hero,
          body
        )
      );
    });
  }

  function renderError(err) {
    show(
      el(
        'section',
        { className: 'notice' },
        el('h1', { text: "Couldn't load the local site" }),
        el('p', { text: String((err && err.message) || err) }),
        el('p', {
          className: 'muted',
          text: 'Check that the worker is running and storage_provider is local.',
        })
      )
    );
  }

  var route = currentRoute();
  var work = route.page === 'post' ? renderPost(route.slug) : renderIndex();
  work.catch(renderError);
})();

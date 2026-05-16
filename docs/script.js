/* ================================================================
   RAG Policy Agent — Documentation Site Script
   Vanilla JS — no frameworks, no build tools
   ================================================================ */

/* ── Page registry ──────────────────────────────────────────────
   Each entry: { file: 'pages/xxx.md', title: 'Display title' }
   "prev" and "next" are derived from order.
   ---------------------------------------------------------------- */
const PAGES = [
  // Getting Started
  { id: 'introduction',       file: 'pages/introduction.md',       title: 'Introduction',       section: 'Getting Started' },
  { id: 'installation',       file: 'pages/installation.md',       title: 'Installation',       section: 'Getting Started' },
  { id: 'quickstart',         file: 'pages/quickstart.md',         title: 'Quickstart',         section: 'Getting Started' },
  // Architecture
  { id: 'architecture',       file: 'pages/architecture.md',       title: 'System Overview',    section: 'Architecture' },
  { id: 'retrieval-pipeline', file: 'pages/retrieval-pipeline.md', title: 'Retrieval Pipeline', section: 'Architecture' },
  { id: 'chunking-strategy',  file: 'pages/chunking-strategy.md',  title: 'Chunking Strategy',  section: 'Architecture' },
  // API Reference
  { id: 'usage',              file: 'pages/usage.md',              title: 'REST API',           section: 'API Reference' },
  // Deployment
  { id: 'docker',             file: 'pages/docker.md',             title: 'Docker',             section: 'Deployment' },
  { id: 'deployment',         file: 'pages/deployment.md',         title: 'Cloud Run',          section: 'Deployment' },
  // Troubleshooting
  { id: 'troubleshooting',    file: 'pages/troubleshooting.md',    title: 'Common Errors',      section: 'Troubleshooting' },
];

/* ── In-memory content cache so we don't re-fetch ────────────── */
const contentCache = {};

/* ── State ───────────────────────────────────────────────────── */
let currentPageId = null;


/* ================================================================
   THEME MANAGEMENT
   ================================================================ */
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('rag-docs-theme', theme);

  // Swap highlight.js CSS
  const hljsLink = document.getElementById('hljs-theme');
  if (hljsLink) {
    hljsLink.href = theme === 'dark'
      ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css'
      : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css';
  }
}

function initTheme() {
  const saved = localStorage.getItem('rag-docs-theme');
  const preferred = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  applyTheme(saved || preferred);
}

document.getElementById('themeToggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  applyTheme(current === 'dark' ? 'light' : 'dark');
});


/* ================================================================
   MOBILE SIDEBAR
   ================================================================ */
const sidebar    = document.getElementById('sidebar');
const hamburger  = document.getElementById('hamburger');
const overlay    = document.getElementById('overlay');

function openSidebar() {
  sidebar.classList.add('open');
  overlay.classList.add('active');
  hamburger.classList.add('active');
  hamburger.setAttribute('aria-expanded', 'true');
  overlay.setAttribute('aria-hidden', 'false');
}

function closeSidebar() {
  sidebar.classList.remove('open');
  overlay.classList.remove('active');
  hamburger.classList.remove('active');
  hamburger.setAttribute('aria-expanded', 'false');
  overlay.setAttribute('aria-hidden', 'true');
}

hamburger.addEventListener('click', () => {
  sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
});
overlay.addEventListener('click', closeSidebar);


/* ================================================================
   MARKED.JS CONFIGURATION  (v14+ token-based API)
   ================================================================ */
function configureMarked() {
  // ── Slug helper (shared by heading renderer and TOC builder) ──
  window.slugify = function(text) {
    return text
      .toLowerCase()
      .replace(/[^\w\s-]/g, '')
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-')
      .trim();
  };

  // In v14 the old new Renderer() + setOptions({renderer}) pattern is gone.
  // The only supported way is marked.use({ renderer: { methodName(token){} } }).
  // Every renderer method now receives a single token object, not positional args.
  // Use this.parser.parseInline(token.tokens) to render child inline tokens.
  marked.use({
    gfm: true,
    breaks: false,
    pedantic: false,
    renderer: {
      // ── Headings: inject id for anchor links and TOC ──────────
      heading(token) {
        // token.depth = 1-6, token.tokens = child inline tokens
        const text = this.parser.parseInline(token.tokens);
        const slug = window.slugify(token.text || text.replace(/<[^>]+>/g, ''));
        return `<h${token.depth} id="${slug}">${text}</h${token.depth}>\n`;
      },

      // ── Tables: wrap in scroll container for mobile ───────────
      // In v14 we must render header cells, body rows, and cells ourselves.
      table(token) {
        // Build header
        let header = '<tr>';
        for (const cell of token.header) {
          const cellText = this.parser.parseInline(cell.tokens);
          const align = cell.align ? ` style="text-align:${cell.align}"` : '';
          header += `<th${align}>${cellText}</th>`;
        }
        header += '</tr>';

        // Build body rows
        let body = '';
        for (const row of token.rows) {
          body += '<tr>';
          for (const cell of row) {
            const cellText = this.parser.parseInline(cell.tokens);
            const align = cell.align ? ` style="text-align:${cell.align}"` : '';
            body += `<td${align}>${cellText}</td>`;
          }
          body += '</tr>';
        }

        return `<div style="overflow-x:auto;"><table><thead>${header}</thead><tbody>${body}</tbody></table></div>\n`;
      },

      // ── Links: open external links in new tab ─────────────────
      link(token) {
        const text = this.parser.parseInline(token.tokens);
        const href = token.href || '';
        const isExternal = href.startsWith('http') || href.startsWith('//');
        const target = isExternal ? ' target="_blank" rel="noopener noreferrer"' : '';
        const title  = token.title ? ` title="${token.title}"` : '';
        return `<a href="${href}"${title}${target}>${text}</a>`;
      },
    },
  });
}


/* ================================================================
   MARKDOWN LOADING & RENDERING
   ================================================================ */
async function fetchMarkdown(filePath) {
  if (contentCache[filePath]) return contentCache[filePath];

  const resp = await fetch(filePath);
  if (!resp.ok) throw new Error(`Failed to load ${filePath} (${resp.status})`);
  const text = await resp.text();
  contentCache[filePath] = text;
  return text;
}

function renderMarkdown(md) {
  return marked.parse(md);
}

function highlightCodeBlocks(container) {
  container.querySelectorAll('pre code').forEach(block => {
    hljs.highlightElement(block);
  });
}

function addCopyButtons(container) {
  container.querySelectorAll('pre').forEach(pre => {
    // Wrap in a relative container
    const wrapper = document.createElement('div');
    wrapper.className = 'code-wrapper';
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'copy';
    btn.setAttribute('aria-label', 'Copy code to clipboard');
    wrapper.appendChild(btn);

    btn.addEventListener('click', async () => {
      const code = pre.querySelector('code');
      const text = code ? code.textContent : pre.textContent;
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = 'copied!';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.textContent = 'copy';
          btn.classList.remove('copied');
        }, 2000);
      } catch {
        btn.textContent = 'error';
        setTimeout(() => { btn.textContent = 'copy'; }, 1500);
      }
    });
  });
}


/* ================================================================
   TABLE OF CONTENTS (auto-generated from headings)
   ================================================================ */
function buildTOC(container) {
  const tocNav = document.getElementById('tocNav');
  tocNav.innerHTML = '';

  const headings = container.querySelectorAll('h2, h3, h4');
  if (headings.length === 0) return;

  const fragment = document.createDocumentFragment();
  headings.forEach(h => {
    const level = parseInt(h.tagName[1]);
    const link = document.createElement('a');
    link.className = `toc-link level-${level}`;
    link.href = `#${h.id}`;
    link.textContent = h.textContent;
    link.addEventListener('click', (e) => {
      e.preventDefault();
      h.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    fragment.appendChild(link);
  });
  tocNav.appendChild(fragment);

  // Intersection observer: highlight active heading
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      const id = entry.target.getAttribute('id');
      const tocLink = tocNav.querySelector(`a[href="#${id}"]`);
      if (!tocLink) return;
      if (entry.isIntersecting) {
        tocNav.querySelectorAll('.toc-link').forEach(l => l.classList.remove('active'));
        tocLink.classList.add('active');
      }
    });
  }, {
    rootMargin: `-${52 + 20}px 0px -70% 0px`,
    threshold: 0,
  });

  headings.forEach(h => observer.observe(h));
}


/* ================================================================
   BREADCRUMB
   ================================================================ */
function updateBreadcrumb(page) {
  const el = document.getElementById('breadcrumbText');
  el.textContent = `Documentation / ${page.section} / ${page.title}`;
}


/* ================================================================
   BOTTOM PAGE NAV (prev / next)
   ================================================================ */
function buildPageNav(pageId) {
  const nav = document.getElementById('pageNav');
  nav.innerHTML = '';

  const idx = PAGES.findIndex(p => p.id === pageId);
  const prev = PAGES[idx - 1];
  const next = PAGES[idx + 1];

  if (prev) {
    const a = document.createElement('a');
    a.className = 'page-nav-link prev';
    a.href = '#';
    a.innerHTML = `<span class="page-nav-direction">← Previous</span><span class="page-nav-label">${prev.title}</span>`;
    a.addEventListener('click', (e) => { e.preventDefault(); loadPage(prev.id); });
    nav.appendChild(a);
  } else {
    nav.appendChild(document.createElement('div')); // placeholder
  }

  if (next) {
    const a = document.createElement('a');
    a.className = 'page-nav-link next';
    a.href = '#';
    a.innerHTML = `<span class="page-nav-direction">Next →</span><span class="page-nav-label">${next.title}</span>`;
    a.addEventListener('click', (e) => { e.preventDefault(); loadPage(next.id); });
    nav.appendChild(a);
  }
}


/* ================================================================
   ACTIVE NAV LINK
   ================================================================ */
function updateNavLinks(pageId) {
  document.querySelectorAll('.nav-link').forEach(link => {
    link.classList.toggle('active', link.dataset.page === pageId);
  });
}


/* ================================================================
   MAIN LOAD PAGE FUNCTION
   ================================================================ */
async function loadPage(pageId) {
  const page = PAGES.find(p => p.id === pageId);
  if (!page) { loadPage('introduction'); return; }

  // Avoid reloading the same page
  if (currentPageId === pageId) return;
  currentPageId = pageId;

  const article = document.getElementById('articleBody');

  // Show loading state
  article.innerHTML = '<div class="loading">Loading content…</div>';
  article.classList.remove('article-enter');

  // Update URL hash (no page reload)
  history.pushState({ pageId }, '', `#${pageId}`);

  // Update UI states immediately
  updateNavLinks(pageId);
  updateBreadcrumb(page);
  closeSidebar();

  // Scroll to top of content
  window.scrollTo({ top: 0, behavior: 'smooth' });

  try {
    const md = await fetchMarkdown(page.file);
    const html = renderMarkdown(md);

    // Inject HTML
    article.innerHTML = html;

    // Trigger fade-in animation
    void article.offsetWidth; // force reflow
    article.classList.add('article-enter');

    // Post-processing
    highlightCodeBlocks(article);
    addCopyButtons(article);
    buildTOC(article);
    buildPageNav(pageId);

  } catch (err) {
    article.innerHTML = `
      <div style="padding: 2rem 0;">
        <h2 style="color: var(--danger);">⚠ Could not load page</h2>
        <p style="color: var(--text-secondary); margin-top: 0.5rem;">${err.message}</p>
        <p style="color: var(--text-tertiary); font-size: 0.84rem; margin-top: 1rem;">
          If running locally, serve this folder with a local HTTP server:<br>
          <code style="font-family: var(--font-mono);">python3 -m http.server 3000</code>
        </p>
      </div>`;
  }
}


/* ================================================================
   URL HASH ROUTING
   ================================================================ */
function routeFromHash() {
  const hash = window.location.hash.replace('#', '').trim();
  const valid = PAGES.find(p => p.id === hash);
  loadPage(valid ? hash : 'introduction');
}

window.addEventListener('popstate', routeFromHash);


/* ================================================================
   SEARCH
   ================================================================ */
const searchInput   = document.getElementById('searchInput');
const searchResults = document.getElementById('searchResults');

// Pre-load all markdown files for full-text search
async function preloadAllContent() {
  await Promise.all(PAGES.map(p => fetchMarkdown(p.file).catch(() => '')));
}

function getExcerpt(md, query, maxLen = 90) {
  const idx = md.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return '';
  const start = Math.max(0, idx - 30);
  const raw = md.slice(start, start + maxLen).replace(/#+\s/g, '').replace(/`/g, '').trim();
  return (start > 0 ? '…' : '') + raw + (start + maxLen < md.length ? '…' : '');
}

function performSearch(query) {
  query = query.trim();

  if (!query) {
    searchResults.hidden = true;
    searchResults.innerHTML = '';
    return;
  }

  const results = [];
  for (const page of PAGES) {
    const md = contentCache[page.file] || '';
    const titleMatch = page.title.toLowerCase().includes(query.toLowerCase());
    const bodyMatch  = md.toLowerCase().includes(query.toLowerCase());

    if (titleMatch || bodyMatch) {
      results.push({
        page,
        excerpt: getExcerpt(md, query),
        titleMatch,
      });
    }
  }

  searchResults.innerHTML = '';

  if (results.length === 0) {
    searchResults.innerHTML = `<div class="search-no-results">No results for "<strong>${escapeHtml(query)}</strong>"</div>`;
    searchResults.hidden = false;
    return;
  }

  // Sort: title matches first
  results.sort((a, b) => b.titleMatch - a.titleMatch);

  results.forEach(({ page, excerpt }) => {
    const item = document.createElement('a');
    item.className = 'search-result-item';
    item.href = '#';
    item.setAttribute('role', 'option');
    item.innerHTML = `
      <span class="search-result-title">${highlightMatch(page.title, query)}</span>
      <span class="search-result-excerpt">${highlightMatch(escapeHtml(excerpt), query)}</span>
    `;
    item.addEventListener('click', (e) => {
      e.preventDefault();
      loadPage(page.id);
      searchInput.value = '';
      searchResults.hidden = true;
    });
    searchResults.appendChild(item);
  });

  searchResults.hidden = false;
}

function highlightMatch(text, query) {
  const regex = new RegExp(`(${escapeRegex(query)})`, 'gi');
  return text.replace(regex, '<mark style="background:var(--accent-subtle);color:var(--accent);border-radius:2px;padding:0 2px;">$1</mark>');
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Debounce search input
let searchTimer = null;
searchInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => performSearch(searchInput.value), 200);
});

// Close search when clicking outside
document.addEventListener('click', (e) => {
  if (!searchResults.contains(e.target) && e.target !== searchInput) {
    searchResults.hidden = true;
  }
});

// Keyboard nav in search results
searchInput.addEventListener('keydown', (e) => {
  const items = [...searchResults.querySelectorAll('.search-result-item')];
  const focused = document.activeElement;
  const idx = items.indexOf(focused);

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    (items[idx + 1] || items[0])?.focus();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    (items[idx - 1] || items[items.length - 1])?.focus();
  } else if (e.key === 'Escape') {
    searchResults.hidden = true;
    searchInput.blur();
  }
});


/* ================================================================
   KEYBOARD SHORTCUTS
   ================================================================ */
document.addEventListener('keydown', (e) => {
  // Cmd/Ctrl + K → focus search
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  }
});


/* ================================================================
   INIT
   ================================================================ */
(function init() {
  // 1. Theme
  initTheme();

  // 2. Configure Marked
  configureMarked();

  // 3. Route to the correct page
  routeFromHash();

  // 4. Pre-load all content in background for search
  setTimeout(preloadAllContent, 1500);
})();

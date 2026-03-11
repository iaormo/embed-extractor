// ========================================
// Xtract - Embed Extractor App
// ========================================

document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  // ---- DOM Elements ----
  const urlInput = document.getElementById('urlInput');
  const extractBtn = document.getElementById('extractBtn');
  const heroSection = document.getElementById('heroSection');
  const sourcesSection = document.getElementById('sourcesSection');
  const resultSection = document.getElementById('resultSection');
  const historySection = document.getElementById('historySection');
  const embedContainer = document.getElementById('embedContainer');
  const resultBadge = document.getElementById('resultBadge');
  const resultStatus = document.getElementById('resultStatus');
  const resultTitle = document.getElementById('resultTitle');
  const resultUrl = document.getElementById('resultUrl');
  const embedCode = document.getElementById('embedCode');
  const codeBlock = document.getElementById('codeBlock');
  const codeToggle = document.getElementById('codeToggle');
  const downloadBtn = document.getElementById('downloadBtn');
  const copyEmbedBtn = document.getElementById('copyEmbedBtn');
  const copyLinkBtn = document.getElementById('copyLinkBtn');
  const openNewTabBtn = document.getElementById('openNewTabBtn');
  const closeResultBtn = document.getElementById('closeResultBtn');
  const historyList = document.getElementById('historyList');
  const clearHistoryBtn = document.getElementById('clearHistoryBtn');
  const toast = document.getElementById('toast');
  const toastMsg = document.getElementById('toastMsg');

  const navBtns = document.querySelectorAll('.nav-btn');
  const hintChips = document.querySelectorAll('.hint-chip');

  // Search DOM
  const searchSection = document.getElementById('searchSection');
  const searchInput = document.getElementById('searchInput');
  const searchBtn = document.getElementById('searchBtn');
  const searchResults = document.getElementById('searchResults');
  const searchLoadMore = document.getElementById('searchLoadMore');
  const loadMoreBtn = document.getElementById('loadMoreBtn');
  const searchWarnings = document.getElementById('searchWarnings');
  const filterChips = document.querySelectorAll('.filter-chip');

  // ---- State ----
  let currentEmbed = null;
  let history = JSON.parse(localStorage.getItem('xtract_history') || '[]');
  let searchState = { query: '', type: 'all', page: 1, results: [], loading: false, hasMore: true };

  // ---- Registration Modal ----
  const registerModal = document.getElementById('registerModal');
  const regName = document.getElementById('regName');
  const regEmail = document.getElementById('regEmail');
  const regPurpose = document.getElementById('regPurpose');
  const regSubmitBtn = document.getElementById('regSubmitBtn');
  const regError = document.getElementById('regError');

  async function checkSession() {
    const token = localStorage.getItem('xtract_session');
    try {
      const url = token ? `/api/session?token=${encodeURIComponent(token)}` : '/api/session';
      const res = await fetch(url);
      const data = await res.json();
      if (data.no_db) return; // No DB configured, skip modal
      if (data.valid) return; // Valid session
      // Invalid or no session — show modal
      localStorage.removeItem('xtract_session');
      registerModal.classList.remove('hidden');
    } catch {
      // Server error or no connectivity — don't block usage
    }
  }

  async function handleRegister() {
    const name = regName.value.trim();
    const email = regEmail.value.trim();
    const purpose = regPurpose.value;

    regError.classList.add('hidden');

    if (!name || !email) {
      regError.textContent = 'Please enter your name and email.';
      regError.classList.remove('hidden');
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      regError.textContent = 'Please enter a valid email address.';
      regError.classList.remove('hidden');
      return;
    }

    regSubmitBtn.disabled = true;
    regSubmitBtn.textContent = 'Registering...';

    try {
      const res = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, purpose })
      });
      const data = await res.json();
      if (data.session_token) {
        localStorage.setItem('xtract_session', data.session_token);
        registerModal.classList.add('hidden');
      } else {
        regError.textContent = data.error || 'Registration failed. Please try again.';
        regError.classList.remove('hidden');
      }
    } catch {
      regError.textContent = 'Connection error. Please try again.';
      regError.classList.remove('hidden');
    } finally {
      regSubmitBtn.disabled = false;
      regSubmitBtn.textContent = 'Get Started';
    }
  }

  if (regSubmitBtn) {
    regSubmitBtn.addEventListener('click', handleRegister);
    regEmail.addEventListener('keydown', (e) => { if (e.key === 'Enter') handleRegister(); });
  }

  // Check session on load
  checkSession();

  // ---- URL Parsers / Extractors ----
  const extractors = {
    // Archive.org
    archiveOrg: {
      name: 'Archive.org',
      test: (url) => /archive\.org\/(details|download|embed)\//.test(url),
      extract: (url) => {
        const urlObj = new URL(url);
        const pathParts = urlObj.pathname.split('/').filter(Boolean);

        // Extract the item identifier (second segment after /details/ or /download/)
        let itemId = null;
        const typeIndex = pathParts.findIndex(p => ['details', 'download', 'embed'].includes(p));
        if (typeIndex !== -1 && pathParts[typeIndex + 1]) {
          itemId = pathParts[typeIndex + 1];
        }

        if (!itemId) return null;

        const embedUrl = `https://archive.org/embed/${itemId}`;
        const directUrl = `https://archive.org/details/${itemId}`;

        return {
          source: 'Archive.org',
          title: decodeURIComponent(itemId).replace(/[-_]/g, ' '),
          embedUrl,
          directUrl,
          embedHtml: `<iframe src="${embedUrl}" width="100%" height="700" frameborder="0" webkitallowfullscreen="true" mozallowfullscreen="true" allowfullscreen></iframe>`,
          type: 'iframe'
        };
      }
    },

    // Google Drive
    googleDrive: {
      name: 'Google Drive',
      test: (url) => /drive\.google\.com\/(file\/d\/|open\?id=)/.test(url),
      extract: (url) => {
        let fileId = null;

        // Format: drive.google.com/file/d/{ID}/view
        const fileMatch = url.match(/\/file\/d\/([a-zA-Z0-9_-]+)/);
        if (fileMatch) {
          fileId = fileMatch[1];
        }

        // Format: drive.google.com/open?id={ID}
        if (!fileId) {
          const idMatch = url.match(/[?&]id=([a-zA-Z0-9_-]+)/);
          if (idMatch) fileId = idMatch[1];
        }

        if (!fileId) return null;

        const embedUrl = `https://drive.google.com/file/d/${fileId}/preview`;
        const directUrl = `https://drive.google.com/file/d/${fileId}/view`;

        return {
          source: 'Google Drive',
          title: `Google Drive Document`,
          embedUrl,
          directUrl,
          embedHtml: `<iframe src="${embedUrl}" width="100%" height="700" frameborder="0" allow="autoplay" allowfullscreen></iframe>`,
          type: 'iframe'
        };
      }
    },

    // Google Docs / Sheets / Slides
    googleDocs: {
      name: 'Google Docs',
      test: (url) => /docs\.google\.com\/(document|spreadsheets|presentation)\/d\//.test(url),
      extract: (url) => {
        const match = url.match(/docs\.google\.com\/(document|spreadsheets|presentation)\/d\/([a-zA-Z0-9_-]+)/);
        if (!match) return null;

        const [, docType, docId] = match;
        const typeNames = {
          document: 'Google Doc',
          spreadsheets: 'Google Sheet',
          presentation: 'Google Slides'
        };

        let embedUrl;
        if (docType === 'presentation') {
          embedUrl = `https://docs.google.com/presentation/d/${docId}/embed?start=false&loop=false&delayms=3000`;
        } else if (docType === 'spreadsheets') {
          embedUrl = `https://docs.google.com/spreadsheets/d/${docId}/htmlview?widget=true`;
        } else {
          embedUrl = `https://docs.google.com/document/d/${docId}/pub?embedded=true`;
        }

        return {
          source: typeNames[docType] || 'Google Docs',
          title: typeNames[docType] || 'Google Document',
          embedUrl,
          directUrl: url,
          embedHtml: `<iframe src="${embedUrl}" width="100%" height="700" frameborder="0"></iframe>`,
          type: 'iframe'
        };
      }
    },

    // Scribd
    scribd: {
      name: 'Scribd',
      test: (url) => /scribd\.com\/(doc|document|book|read)\//.test(url),
      extract: (url) => {
        const match = url.match(/scribd\.com\/(doc|document|book|read)\/(\d+)/);
        if (!match) return null;

        const docId = match[2];
        const embedUrl = `https://www.scribd.com/embeds/${docId}/content`;

        return {
          source: 'Scribd',
          title: `Scribd Document #${docId}`,
          embedUrl,
          directUrl: url,
          embedHtml: `<iframe src="${embedUrl}" width="100%" height="700" frameborder="0" scrolling="no"></iframe>`,
          type: 'iframe'
        };
      }
    },

    // Direct PDF
    directPdf: {
      name: 'Direct PDF',
      test: (url) => /\.pdf(\?.*)?$/i.test(url),
      extract: (url) => {
        return {
          source: 'PDF',
          title: decodeURIComponent(url.split('/').pop().replace(/\.pdf.*/i, '')).replace(/[-_]/g, ' '),
          embedUrl: url,
          directUrl: url,
          embedHtml: `<iframe src="${url}" width="100%" height="700" frameborder="0"></iframe>`,
          type: 'pdf'
        };
      }
    },

    // Issuu
    issuu: {
      name: 'Issuu',
      test: (url) => /issuu\.com\//.test(url),
      extract: (url) => {
        const match = url.match(/issuu\.com\/([^/]+)\/docs\/([^/?]+)/);
        if (!match) return null;

        const username = match[1];
        const slug = match[2];
        const embedUrl = `https://e.issuu.com/embed.html?d=${slug}&u=${username}`;

        return {
          source: 'Issuu',
          title: decodeURIComponent(slug).replace(/[-_]/g, ' '),
          embedUrl,
          directUrl: url,
          embedHtml: `<iframe src="${embedUrl}" width="100%" height="700" frameborder="0" allowfullscreen></iframe>`,
          type: 'iframe',
          issuuUsername: username,
          issuuSlug: slug,
        };
      }
    },

    // SlideShare
    slideshare: {
      name: 'SlideShare',
      test: (url) => /slideshare\.net\//.test(url),
      extract: (url) => {
        const embedUrl = `https://www.slideshare.net/slideshow/embed_code/key/${encodeURIComponent(url)}`;

        return {
          source: 'SlideShare',
          title: 'SlideShare Presentation',
          embedUrl: url,
          directUrl: url,
          embedHtml: `<iframe src="${url}" width="100%" height="700" frameborder="0" allowfullscreen></iframe>`,
          type: 'iframe'
        };
      }
    },

    // Generic fallback - attempt iframe embed
    generic: {
      name: 'Generic',
      test: () => true,
      extract: (url) => {
        return {
          source: 'Embed',
          title: new URL(url).hostname,
          embedUrl: url,
          directUrl: url,
          embedHtml: `<iframe src="${url}" width="100%" height="700" frameborder="0" allowfullscreen sandbox="allow-scripts allow-same-origin allow-popups"></iframe>`,
          type: 'iframe'
        };
      }
    }
  };

  // ---- Core Functions ----

  function extractEmbed(url) {
    url = url.trim();
    if (!url) return null;

    // Add protocol if missing
    if (!/^https?:\/\//i.test(url)) {
      url = 'https://' + url;
    }

    try {
      new URL(url); // validate
    } catch {
      return null;
    }

    // Try each extractor
    for (const key of Object.keys(extractors)) {
      const ext = extractors[key];
      if (ext.test(url)) {
        const result = ext.extract(url);
        if (result) {
          result.originalUrl = url;
          return result;
        }
      }
    }

    return null;
  }

  // Sites known to block iframe embedding
  const IFRAME_BLOCKED_DOMAINS = [
    'facebook.com', 'fb.watch', 'fb.com',
    'instagram.com', 'tiktok.com',
    'twitter.com', 'x.com',
    'reddit.com', 'twitch.tv',
    'soundcloud.com', 'bandcamp.com',
  ];

  function isIframeBlocked(url) {
    try {
      const host = new URL(url).hostname.replace(/^www\./, '');
      return IFRAME_BLOCKED_DOMAINS.some(d => host === d || host.endsWith('.' + d));
    } catch { return false; }
  }

  function showResult(embed) {
    currentEmbed = embed;

    resultBadge.textContent = embed.source;
    resultStatus.textContent = '';
    resultStatus.className = 'result-status';
    resultTitle.textContent = capitalize(embed.title);
    resultUrl.textContent = embed.originalUrl;
    embedCode.textContent = embed.embedHtml;

    const url = embed.directUrl || embed.embedUrl;

    // For sites that block iframes, show thumbnail preview + warning instead
    if (isIframeBlocked(url) && isYtdlpUrl(url)) {
      embedContainer.innerHTML = `
        <div class="embed-loading">
          <div class="loader-ring"></div>
          <span>Fetching preview...</span>
        </div>
      `;

      // Fetch thumbnail from yt-dlp
      extractWithYtdlp(url).then(data => {
        const entry = data.entries?.[0];
        const thumb = entry?.thumbnail;
        const title = entry?.title || embed.title;
        const duration = entry?.duration ? formatDuration(entry.duration) : '';

        embedContainer.innerHTML = `
          <div class="iframe-blocked-preview">
            ${thumb ? `<img src="${thumb}" alt="${title}" class="preview-thumbnail" />` : ''}
            <div class="preview-overlay">
              <div class="preview-warning">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                <span>This site blocks iframe previews. Use the Download button to save the media.</span>
              </div>
              ${duration ? `<span class="preview-duration">${duration}</span>` : ''}
            </div>
          </div>
        `;
      }).catch(() => {
        embedContainer.innerHTML = `
          <div class="iframe-blocked-preview">
            <div class="preview-overlay" style="position:relative;">
              <div class="preview-warning">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                <span>This site blocks iframe previews. Use the Download button to save the media.</span>
              </div>
            </div>
          </div>
        `;
      });
    } else {
      // Normal iframe embed flow
      embedContainer.innerHTML = `
        <div class="embed-loading">
          <div class="loader-ring"></div>
          <span>Loading embed...</span>
        </div>
      `;

      const iframe = document.createElement('iframe');
      iframe.src = embed.embedUrl;
      iframe.width = '100%';
      iframe.height = '700';
      iframe.frameBorder = '0';
      iframe.allowFullscreen = true;
      iframe.setAttribute('webkitallowfullscreen', 'true');
      iframe.setAttribute('mozallowfullscreen', 'true');

      if (embed.type !== 'pdf') {
        iframe.sandbox = 'allow-scripts allow-same-origin allow-popups allow-forms';
      }

      const removeLoader = () => {
        const loader = embedContainer.querySelector('.embed-loading');
        if (loader) loader.remove();
      };

      iframe.onload = removeLoader;
      setTimeout(removeLoader, 4000);

      iframe.onerror = () => {
        embedContainer.innerHTML = `
          <div class="embed-loading">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:0.3">
              <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
            </svg>
            <span>Failed to load embed. The source may block iframe embedding.</span>
          </div>
        `;
      };

      embedContainer.appendChild(iframe);

      if (embed.source === 'Archive.org') {
        iframe.removeAttribute('sandbox');
      }
    }

    // Show/hide sections
    resultSection.classList.remove('hidden');
    sourcesSection.classList.add('hidden');

    // Reset code toggle
    codeBlock.classList.add('hidden');
    codeToggle.classList.remove('open');

    // Scroll to result
    resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

    // Add to history
    addToHistory(embed);
  }

  function hideResult() {
    resultSection.classList.add('hidden');
    sourcesSection.classList.remove('hidden');
    embedContainer.innerHTML = '';
    currentEmbed = null;
    const dlFiles = document.querySelector('.download-files');
    if (dlFiles) dlFiles.remove();
  }

  // ---- History ----

  function addToHistory(embed) {
    const entry = {
      source: embed.source,
      title: embed.title,
      url: embed.originalUrl,
      embedUrl: embed.embedUrl,
      timestamp: Date.now()
    };

    // Remove duplicate
    history = history.filter(h => h.url !== entry.url);
    history.unshift(entry);

    // Keep max 50
    if (history.length > 50) history = history.slice(0, 50);

    localStorage.setItem('xtract_history', JSON.stringify(history));
    renderHistory();
  }

  function renderHistory() {
    if (history.length === 0) {
      historyList.innerHTML = `
        <div class="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
          </svg>
          <p>No extraction history yet</p>
          <span>Your extracted embeds will appear here</span>
        </div>
      `;
      return;
    }

    historyList.innerHTML = history.map(item => `
      <div class="history-item" data-url="${escapeAttr(item.url)}">
        <div class="history-item-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>
          </svg>
        </div>
        <div class="history-item-info">
          <div class="history-item-title">${escapeHtml(capitalize(item.title))}</div>
          <div class="history-item-url">${escapeHtml(item.url)}</div>
        </div>
        <div class="history-item-time">${timeAgo(item.timestamp)}</div>
      </div>
    `).join('');

    // Add click listeners
    historyList.querySelectorAll('.history-item').forEach(el => {
      el.addEventListener('click', () => {
        const url = el.dataset.url;
        urlInput.value = url;
        switchTab('extract');
        handleExtract();
      });
    });
  }

  // ---- Event Handlers ----

  function handleExtract() {
    const url = urlInput.value.trim();
    if (!url) {
      showToast('Please enter a URL', true);
      urlInput.focus();
      return;
    }

    extractBtn.classList.add('loading');

    // Small delay for UX
    setTimeout(() => {
      const result = extractEmbed(url);
      extractBtn.classList.remove('loading');

      if (result) {
        showResult(result);
      } else {
        showToast('Could not extract embed from this URL', true);
      }
    }, 400);
  }

  function switchTab(tab) {
    navBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    if (tab === 'extract') {
      heroSection.classList.remove('hidden');
      historySection.classList.add('hidden');
      searchSection.classList.add('hidden');
      if (!currentEmbed) {
        sourcesSection.classList.remove('hidden');
      }
      if (currentEmbed) {
        resultSection.classList.remove('hidden');
      }
    } else if (tab === 'history') {
      heroSection.classList.add('hidden');
      sourcesSection.classList.add('hidden');
      resultSection.classList.add('hidden');
      searchSection.classList.add('hidden');
      historySection.classList.remove('hidden');
      renderHistory();
    } else if (tab === 'search') {
      heroSection.classList.add('hidden');
      sourcesSection.classList.add('hidden');
      resultSection.classList.add('hidden');
      historySection.classList.add('hidden');
      searchSection.classList.remove('hidden');
      setTimeout(() => searchInput.focus(), 100);
    }
  }

  function showToast(msg, isError = false) {
    toastMsg.textContent = msg;
    toast.classList.toggle('error', isError);
    toast.classList.remove('hidden');

    requestAnimationFrame(() => {
      toast.classList.add('visible');
    });

    setTimeout(() => {
      toast.classList.remove('visible');
      setTimeout(() => toast.classList.add('hidden'), 300);
    }, 2500);
  }

  async function copyToClipboard(text, btnEl) {
    try {
      await navigator.clipboard.writeText(text);
      if (btnEl) {
        btnEl.classList.add('copied');
        setTimeout(() => btnEl.classList.remove('copied'), 1500);
      }
      showToast('Copied to clipboard');
    } catch {
      showToast('Failed to copy', true);
    }
  }

  // ---- Download Logic ----

  async function fetchArchiveFiles(itemId) {
    // Use local proxy to bypass CORS
    const res = await fetch(`/api/metadata?id=${encodeURIComponent(itemId)}`);
    if (!res.ok) throw new Error('Failed to fetch metadata');
    const data = await res.json();
    const files = data.result || data;
    // Skip metadata/derivative junk files
    const skipExtensions = ['.xml', '.sqlite', '.torrent', '.log', '.mrc', '.gz', '.json'];
    const skipNames = ['__ia_thumb.jpg'];
    return files.filter(f => {
      const name = (f.name || '').toLowerCase();
      if (skipNames.includes(name)) return false;
      if (skipExtensions.some(ext => name.endsWith(ext))) return false;
      if (name.endsWith('_meta.xml') || name.endsWith('_files.xml')) return false;
      if (name.startsWith('__')) return false;
      // Skip DRM-encrypted files (can't be opened anyway)
      if (name.includes('encrypted') || (f.format || '').includes('Encrypted')) return false;
      if (name.endsWith('.lcpdf') || name.endsWith('.lcp.epub')) return false;
      if ((f.format || '').startsWith('LCP ')) return false;
      return true;
    }).map(f => {
      const isRestricted = f.private === 'true' || f.private === true;
      return {
        name: f.name,
        size: f.size ? formatBytes(parseInt(f.size, 10)) : 'Unknown size',
        format: f.format || f.name.split('.').pop().toUpperCase(),
        restricted: isRestricted,
        downloadUrl: `/api/download?url=${encodeURIComponent(`https://archive.org/download/${itemId}/${f.name}`)}&filename=${encodeURIComponent(f.name)}`,
        directUrl: `https://archive.org/download/${itemId}/${encodeURIComponent(f.name)}`
      };
    });
  }

  function formatBytes(bytes) {
    if (!bytes || isNaN(bytes)) return 'Unknown size';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function showDownloadFiles(files, itemId) {
    // Remove existing download list
    const existing = document.querySelector('.download-files');
    if (existing) existing.remove();

    // Only show free files
    const freeFiles = files.filter(f => !f.restricted);
    const contentExts = ['.pdf', '.epub', '.djvu', '.txt', '.mp4', '.mp3', '.ogg', '.flac', '.png', '.jpg', '.jpeg', '.webm', '.ogv', '.zip', '.tar'];
    const freeContentFiles = freeFiles.filter(f => contentExts.some(ext => f.name.toLowerCase().endsWith(ext)));

    // Update the result status tag
    if (resultStatus) {
      if (freeContentFiles.length > 0) {
        resultStatus.textContent = 'Downloadable';
        resultStatus.className = 'result-status downloadable';
      } else if (files.length > 0) {
        resultStatus.textContent = 'Restricted';
        resultStatus.className = 'result-status restricted';
      } else {
        resultStatus.textContent = '';
        resultStatus.className = 'result-status';
      }
    }

    if (freeFiles.length === 0) {
      showToast('All files are restricted — not freely downloadable', true);
      return;
    }

    const container = document.createElement('div');
    container.className = 'download-files';
    container.innerHTML = `
      <div class="download-files-header">
        <h4>Available Downloads</h4>
        <span>${freeFiles.length} free file${freeFiles.length > 1 ? 's' : ''}</span>
      </div>
      ${freeFiles.map((f, i) => `
        <div class="download-file-item">
          <div class="download-file-icon">${f.format.substring(0, 4)}</div>
          <div class="download-file-info">
            <div class="download-file-name">${escapeHtml(f.name)} <span class="file-tag free-tag">Free</span></div>
            <div class="download-file-size">${f.size}</div>
          </div>
          <button class="download-file-btn" data-idx="${i}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Download
          </button>
        </div>
      `).join('')}
    `;

    // Insert after embed container
    embedContainer.after(container);

    // Attach click handlers for each file download button
    container.querySelectorAll('.download-file-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        const idx = parseInt(btn.dataset.idx, 10);
        const file = freeFiles[idx];
        if (!file) return;
        btn.textContent = 'Downloading...';
        btn.disabled = true;
        forceDownload(file.downloadUrl, file.name);
        btn.textContent = 'Done!';
        setTimeout(() => {
          btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download`;
          btn.disabled = false;
        }, 2000);
      });
    });
  }

  // Force download via local proxy — bypasses CORS and forces Content-Disposition: attachment
  function forceDownload(proxyUrl, filename) {
    showToast('Downloading: ' + filename);
    const a = document.createElement('a');
    a.href = proxyUrl;
    a.download = filename || 'download';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => document.body.removeChild(a), 1000);
  }

  async function handleIssuuDownload() {
    const { issuuUsername, issuuSlug, title } = currentEmbed;
    if (!issuuUsername || !issuuSlug) {
      showToast('Cannot determine Issuu document info', true);
      return;
    }

    // First fetch metadata to show page count
    showToast('Fetching document info...');
    try {
      const metaRes = await fetch(`/api/issuu-meta?username=${encodeURIComponent(issuuUsername)}&slug=${encodeURIComponent(issuuSlug)}`);
      if (!metaRes.ok) throw new Error('Failed to fetch metadata');
      const meta = await metaRes.json();

      // Show file list with single PDF entry
      const existing = document.querySelector('.download-files');
      if (existing) existing.remove();

      const container = document.createElement('div');
      container.className = 'download-files';
      container.innerHTML = `
        <div class="download-files-header">
          <h4>${escapeHtml(meta.title || title)}</h4>
          <span>${meta.pageCount} pages</span>
        </div>
        <div class="download-file-item">
          <div class="download-file-icon">PDF</div>
          <div class="download-file-info">
            <div class="download-file-name">${escapeHtml(meta.title || title)}.pdf</div>
            <div class="download-file-size">${meta.pageCount} pages — server will build PDF from page images</div>
          </div>
          <button class="download-file-btn" id="issuuPdfBtn">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Build & Download PDF
          </button>
        </div>
      `;
      embedContainer.after(container);

      // Also auto-trigger the PDF build
      triggerIssuuPdfDownload(issuuUsername, issuuSlug, meta.title || title);

      // Wire up the button for re-download
      document.getElementById('issuuPdfBtn').addEventListener('click', () => {
        triggerIssuuPdfDownload(issuuUsername, issuuSlug, meta.title || title);
      });

    } catch (err) {
      showToast('Failed to get Issuu info: ' + err.message, true);
    }
  }

  function triggerIssuuPdfDownload(username, slug, title) {
    showToast('Building PDF from page images... this may take a moment');
    const pdfUrl = `/api/issuu-pdf?username=${encodeURIComponent(username)}&slug=${encodeURIComponent(slug)}`;
    forceDownload(pdfUrl, `${title}.pdf`);
  }

  async function handleScribdDownload() {
    const url = currentEmbed.directUrl || currentEmbed.embedUrl;
    const match = url.match(/scribd\.com\/(?:doc|document|book|read)\/(\d+)/);
    if (!match) {
      showToast('Cannot determine Scribd document ID', true);
      return;
    }
    const docId = match[1];
    showToast('Building PDF from Scribd pages — this may take a moment...');

    try {
      const res = await fetch(`/api/scribd-pdf?doc_id=${encodeURIComponent(docId)}`);
      if (res.ok) {
        const blob = await res.blob();
        // Extract filename from Content-Disposition header
        const cd = res.headers.get('Content-Disposition') || '';
        const fnMatch = cd.match(/filename\*=UTF-8''(.+)/);
        const filename = fnMatch ? decodeURIComponent(fnMatch[1]) : `scribd_${docId}.pdf`;
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(blobUrl); }, 1000);
        showToast(`Downloaded: ${filename}`);
      } else {
        const data = await res.json().catch(() => null);
        const msg = data?.error || 'Scribd download failed — the document may be private or unavailable.';
        showToast(msg, true);
      }
    } catch (err) {
      showToast('Scribd download failed: ' + err.message, true);
    }
  }

  async function handleSlideShareDownload() {
    const url = currentEmbed.directUrl || currentEmbed.embedUrl;
    showToast('Downloading SlideShare presentation... this may take a moment');
    const pdfUrl = `/api/slideshare-pdf?url=${encodeURIComponent(url)}`;
    const title = currentEmbed.title || 'slideshare';
    forceDownload(pdfUrl, `${title}.pdf`);
  }

  // ---- yt-dlp Extraction (videos, audio from social media, etc.) ----

  const YTDLP_DOMAINS = [
    'facebook.com', 'fb.watch', 'fb.com',
    'youtube.com', 'youtu.be',
    'twitter.com', 'x.com',
    'instagram.com',
    'tiktok.com',
    'vimeo.com',
    'dailymotion.com',
    'twitch.tv',
    'soundcloud.com',
    'bandcamp.com',
    'reddit.com',
    'streamable.com',
    'bilibili.com',
  ];

  function isYtdlpUrl(url) {
    try {
      const host = new URL(url).hostname.replace(/^www\./, '');
      return YTDLP_DOMAINS.some(d => host === d || host.endsWith('.' + d));
    } catch { return false; }
  }

  async function extractWithYtdlp(url) {
    const res = await fetch(`/api/extract?url=${encodeURIComponent(url)}`);
    if (!res.ok) throw new Error('Extraction failed');
    return await res.json();
  }

  function showExtractedFiles(entries, originalUrl) {
    const existing = document.querySelector('.download-files');
    if (existing) existing.remove();

    if (!entries || entries.length === 0) {
      showToast('No downloadable media found', true);
      return;
    }

    const container = document.createElement('div');
    container.className = 'download-files';
    container.innerHTML = `
      <div class="download-files-header">
        <h4>Available Downloads</h4>
        <span>${entries.length} file${entries.length > 1 ? 's' : ''}</span>
      </div>
      ${entries.map((entry, i) => {
        const size = entry.filesize ? formatBytes(entry.filesize) : '';
        const dur = entry.duration ? `${Math.floor(entry.duration / 60)}:${String(Math.floor(entry.duration % 60)).padStart(2, '0')}` : '';
        const meta = [size, dur, entry.ext?.toUpperCase()].filter(Boolean).join(' · ');
        return `
        <div class="download-file-item">
          <div class="download-file-icon">${entry.type === 'audio' ? '♫' : '▶'}</div>
          <div class="download-file-info">
            <div class="download-file-name">${escapeHtml(entry.title || 'Media')}</div>
            <div class="download-file-size">${meta}</div>
          </div>
          <button class="download-file-btn" data-idx="${i}" data-type="video">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Video
          </button>
          <button class="download-file-btn" data-idx="${i}" data-type="audio" style="background: var(--ink-secondary);">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
            Audio
          </button>
        </div>
      `}).join('')}
    `;

    embedContainer.after(container);

    container.querySelectorAll('.download-file-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.dataset.idx, 10);
        const type = btn.dataset.type;
        const entry = entries[idx];
        if (!entry) return;

        btn.textContent = 'Downloading...';
        btn.disabled = true;

        const entryUrl = entry.url || originalUrl;
        const audioParam = type === 'audio' ? '&audio=true' : '';
        const dlUrl = `/api/extract-download?url=${encodeURIComponent(entryUrl)}${audioParam}`;
        const ext = type === 'audio' ? '.mp3' : `.${entry.ext || 'mp4'}`;
        const filename = (entry.title || 'download') + ext;

        forceDownload(dlUrl, filename);

        setTimeout(() => {
          btn.textContent = type === 'audio' ? 'Audio' : 'Video';
          btn.disabled = false;
        }, 3000);
      });
    });
  }

  function guessFilename(url, title) {
    // Try to get filename from URL path
    try {
      const pathname = new URL(url).pathname;
      const lastSegment = decodeURIComponent(pathname.split('/').filter(Boolean).pop() || '');
      if (lastSegment && lastSegment.includes('.')) return lastSegment;
    } catch {}
    // Fallback: use title + guess extension from URL
    const ext = guessExtension(url);
    return (title || 'download') + ext;
  }

  function guessExtension(url) {
    const u = url.toLowerCase();
    if (u.match(/\.(pdf)(\?|$)/)) return '.pdf';
    if (u.match(/\.(mp4|webm|mkv|avi|mov)(\?|$)/)) return '.' + u.match(/\.(mp4|webm|mkv|avi|mov)/)[1];
    if (u.match(/\.(mp3|wav|flac|ogg|aac|m4a)(\?|$)/)) return '.' + u.match(/\.(mp3|wav|flac|ogg|aac|m4a)/)[1];
    if (u.match(/\.(jpg|jpeg|png|gif|webp|svg|bmp)(\?|$)/)) return '.' + u.match(/\.(jpg|jpeg|png|gif|webp|svg|bmp)/)[1];
    if (u.match(/\.(doc|docx|xls|xlsx|ppt|pptx)(\?|$)/)) return '.' + u.match(/\.(doc|docx|xls|xlsx|ppt|pptx)/)[1];
    if (u.match(/\.(epub|djvu|mobi|txt|zip|rar|7z)(\?|$)/)) return '.' + u.match(/\.(epub|djvu|mobi|txt|zip|rar|7z)/)[1];
    return '';
  }

  async function handleDownload() {
    if (!currentEmbed) return;

    downloadBtn.classList.add('loading');

    try {
      if (currentEmbed.source === 'Archive.org') {
        // Extract item ID from embed URL
        const itemId = currentEmbed.embedUrl.split('/embed/')[1];
        const files = await fetchArchiveFiles(itemId);
        showDownloadFiles(files, itemId);

        const dlExts = ['.pdf', '.epub', '.djvu', '.txt', '.mp4', '.mp3', '.ogg', '.flac', '.png', '.jpg', '.jpeg', '.webm', '.ogv', '.zip', '.tar'];
        if (files.length > 0) {
          // Auto-download: prefer free PDF, then free EPUB, then show list
          const freePdf = files.find(f => !f.restricted && f.name.toLowerCase().endsWith('.pdf'));
          const freeEpub = files.find(f => !f.restricted && f.name.toLowerCase().endsWith('.epub'));
          const freeContent = files.find(f => !f.restricted && dlExts.some(ext => f.name.toLowerCase().endsWith(ext)));
          const target = freePdf || freeEpub || freeContent;
          if (target) {
            forceDownload(target.downloadUrl, target.name);
          } else {
            const freeCount = files.filter(f => !f.restricted).length;
            if (freeCount > 0) {
              showToast(`${freeCount} free files found. Pick one from the list below.`);
            } else {
              showToast('All files are restricted — not freely downloadable', true);
            }
          }
        } else {
          showToast('No content files found for this item', true);
        }
      } else if (currentEmbed.source === 'Issuu') {
        await handleIssuuDownload();
      } else if (currentEmbed.source === 'Scribd') {
        await handleScribdDownload();
      } else if (currentEmbed.source === 'SlideShare') {
        await handleSlideShareDownload();
      } else if (currentEmbed.source === 'Google Drive') {
        const fileId = currentEmbed.embedUrl.match(/\/file\/d\/([^/]+)/)?.[1];
        if (fileId) {
          const dlUrl = `https://drive.google.com/uc?export=download&id=${fileId}`;
          const filename = guessFilename(currentEmbed.directUrl, currentEmbed.title);
          const proxyUrl = `/api/download?url=${encodeURIComponent(dlUrl)}&filename=${encodeURIComponent(filename)}`;
          forceDownload(proxyUrl, filename);
        }
      } else {
        // Try yt-dlp extraction first for known video/social sites
        const url = currentEmbed.directUrl || currentEmbed.embedUrl;
        if (isYtdlpUrl(url)) {
          showToast('Extracting media info...');
          try {
            const data = await extractWithYtdlp(url);
            if (data.supported && data.entries && data.entries.length > 0) {
              showExtractedFiles(data.entries, url);
              // Auto-trigger first video download
              const dlUrl = `/api/extract-download?url=${encodeURIComponent(data.entries[0].url || url)}`;
              const filename = (data.entries[0].title || 'download') + '.' + (data.entries[0].ext || 'mp4');
              forceDownload(dlUrl, filename);
            } else {
              // Fallback to brute force proxy
              const filename = guessFilename(url, currentEmbed.title);
              const proxyUrl = `/api/download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(filename)}`;
              forceDownload(proxyUrl, filename);
            }
          } catch {
            const filename = guessFilename(url, currentEmbed.title);
            const proxyUrl = `/api/download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(filename)}`;
            forceDownload(proxyUrl, filename);
          }
        } else {
          // Try to resolve direct file URL for known sites
          const resolvedUrl = resolveDirectFileUrl(url);
          const filename = guessFilename(resolvedUrl, currentEmbed.title);
          const proxyUrl = `/api/download?url=${encodeURIComponent(resolvedUrl)}&filename=${encodeURIComponent(filename)}`;
          forceDownload(proxyUrl, filename);
        }
      }
    } catch (err) {
      showToast('Download failed: ' + err.message, true);
    } finally {
      downloadBtn.classList.remove('loading');
    }
  }

  // ---- Helpers ----

  function resolveDirectFileUrl(url) {
    try {
      const u = new URL(url);
      const host = u.hostname;
      const path = u.pathname;

      // MDPI (open access) — use CDN URL for PDF
      if (host.includes('mdpi.com')) {
        const mdpiMatch = path.match(/^\/(\d{4}-\d{4})\/(\d+)\/(\d+)\/(\d+)/);
        if (mdpiMatch) {
          const [, journalId, volume, issue, articleId] = mdpiMatch;
          // Common MDPI journal ID to slug mapping
          const mdpiJournals = {
            '2076-3417': 'applsci', '1424-8220': 'sensors', '2071-1050': 'sustainability',
            '1996-1073': 'energies', '2079-9292': 'electronics', '1660-4601': 'ijerph',
            '2227-7390': 'mathematics', '1420-3049': 'molecules', '2072-4292': 'remotesensing',
            '1999-4893': 'algorithms', '2073-4425': 'genes', '2076-2607': 'microorganisms',
            '2079-6374': 'biosensors', '2075-4418': 'diagnostics', '2072-666X': 'micromachines',
            '2076-3921': 'antioxidants', '2304-6740': 'jof', '2079-4991': 'nanomaterials',
            '2073-4441': 'water', '2076-3298': 'environments', '2075-1729': 'life',
            '2077-0383': 'jcm', '2305-6304': 'fermentation', '2079-7737': 'biology',
            '2079-8954': 'systems', '2227-9040': 'chemosensors', '2073-4360': 'polymers',
            '2076-0817': 'pathogens', '2079-9268': 'asi', '1999-4907': 'forests',
            '2075-163X': 'minerals', '2304-8158': 'foods', '2076-2615': 'animals',
            '2073-4395': 'agronomy', '2078-2489': 'info', '2075-4442': 'catalysts',
            '2227-9717': 'processes', '2218-273X': 'biomolecules', '2073-8994': 'symmetry',
            '1099-4300': 'entropy', '2079-3197': 'computation', '2076-3387': 'admsci',
          };
          const slug = mdpiJournals[journalId];
          if (slug) {
            return `https://mdpi-res.com/d_attachment/${slug}/${slug}-${volume.padStart(2,'0')}-${articleId.padStart(5,'0')}/article_deploy/${slug}-${volume.padStart(2,'0')}-${articleId.padStart(5,'0')}.pdf`;
          }
        }
      }
      // arXiv — convert abstract page to PDF
      if (host.includes('arxiv.org') && path.startsWith('/abs/')) {
        return url.replace('/abs/', '/pdf/') + '.pdf';
      }
      // PubMed Central — construct PDF URL from article ID
      if (host.includes('ncbi.nlm.nih.gov') && path.includes('/pmc/articles/PMC')) {
        const pmcId = path.match(/PMC\d+/)?.[0];
        if (pmcId) return `https://www.ncbi.nlm.nih.gov/pmc/articles/${pmcId}/pdf/`;
      }
      // IEEE Xplore — not open access but try
      if (host.includes('ieeexplore.ieee.org') && path.includes('/document/')) {
        const docId = path.match(/\/document\/(\d+)/)?.[1];
        if (docId) return `https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=${docId}`;
      }
      // Semantic Scholar — redirect to PDF if available
      if (host.includes('semanticscholar.org') && path.includes('/paper/')) {
        return url;
      }
      // ResearchGate — add .pdf
      if (host.includes('researchgate.net') && path.includes('/publication/')) {
        return url;
      }
      // Direct PDF link detection — if URL already ends with .pdf, use as-is
      if (path.endsWith('.pdf')) return url;

      // Fallback: try appending .pdf or /pdf for unknown sites
      return url;
    } catch {
      return url;
    }
  }

  function capitalize(str) {
    return str.replace(/\b\w/g, l => l.toUpperCase());
  }

  function formatDuration(seconds) {
    if (!seconds) return '';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    return `${m}:${String(s).padStart(2,'0')}`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return str.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function timeAgo(ts) {
    const diff = Date.now() - ts;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(ts).toLocaleDateString();
  }

  // ---- Event Bindings ----

  extractBtn.addEventListener('click', handleExtract);

  urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleExtract();
  });

  navBtns.forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  hintChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const url = chip.dataset.url;
      if (url) {
        urlInput.value = url;
        urlInput.focus();
      } else {
        urlInput.focus();
      }
    });
  });

  downloadBtn.addEventListener('click', handleDownload);

  closeResultBtn.addEventListener('click', hideResult);

  copyEmbedBtn.addEventListener('click', () => {
    if (currentEmbed) {
      copyToClipboard(currentEmbed.embedHtml, copyEmbedBtn);
    }
  });

  copyLinkBtn.addEventListener('click', () => {
    if (currentEmbed) {
      copyToClipboard(currentEmbed.embedUrl, copyLinkBtn);
    }
  });

  openNewTabBtn.addEventListener('click', () => {
    if (currentEmbed) {
      window.open(currentEmbed.directUrl, '_blank', 'noopener');
    }
  });

  codeToggle.addEventListener('click', () => {
    codeBlock.classList.toggle('hidden');
    codeToggle.classList.toggle('open');
  });

  clearHistoryBtn.addEventListener('click', () => {
    history = [];
    localStorage.removeItem('xtract_history');
    renderHistory();
    showToast('History cleared');
  });

  // ---- Search Logic ----

  async function performSearch(append = false) {
    const query = searchInput.value.trim();
    if (!query) {
      showToast('Please enter a search topic', true);
      searchInput.focus();
      return;
    }

    if (!append) {
      searchState.query = query;
      searchState.page = 1;
      searchState.results = [];
      searchState.hasMore = true;
      searchResults.innerHTML = '';
    }

    searchState.loading = true;
    searchBtn.classList.add('loading');
    if (append) loadMoreBtn.classList.add('loading');

    try {
      const params = new URLSearchParams({
        q: searchState.query,
        type: searchState.type,
        page: searchState.page,
        per_page: 20,
      });

      const res = await fetch(`/api/search?${params}`);
      if (!res.ok) throw new Error('Search failed');
      const data = await res.json();

      if (data.warnings && data.warnings.length > 0) {
        searchWarnings.classList.remove('hidden');
        searchWarnings.innerHTML = data.warnings
          .map(w => `<div class="search-warning-item">${escapeHtml(w)}</div>`).join('');
      } else {
        searchWarnings.classList.add('hidden');
      }

      searchState.results.push(...data.results);
      searchState.hasMore = data.results.length >= 20;

      if (searchState.results.length === 0) {
        searchResults.innerHTML = `
          <div class="empty-state">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <p>No results found</p>
            <span>Try a different search term or content type</span>
          </div>`;
        searchLoadMore.classList.add('hidden');
      } else {
        if (!append) searchResults.innerHTML = '';
        renderSearchResults(data.results);
        searchLoadMore.classList.toggle('hidden', !searchState.hasMore);
      }
    } catch (err) {
      showToast('Search failed: ' + err.message, true);
    } finally {
      searchState.loading = false;
      searchBtn.classList.remove('loading');
      loadMoreBtn.classList.remove('loading');
    }
  }

  function renderSearchResults(results) {
    const fragment = document.createDocumentFragment();

    results.forEach(item => {
      const card = document.createElement('div');
      card.className = 'search-result-card';

      const thumbHtml = item.thumbnail
        ? `<img src="${escapeAttr(item.thumbnail)}" alt="" class="search-result-thumb" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
        : '';

      const typeIcon = getTypeIcon(item.media_type);
      const sourceBadge = getSourceLabel(item.source);
      const extraInfo = buildExtraInfo(item);

      card.innerHTML = `
        <div class="search-result-thumb-container">
          ${thumbHtml}
          <div class="search-result-thumb-placeholder" style="${item.thumbnail ? 'display:none' : ''}">
            ${typeIcon}
          </div>
          <span class="search-result-type-badge">${escapeHtml(item.media_type)}</span>
        </div>
        <div class="search-result-info">
          <div class="search-result-source">${sourceBadge}</div>
          <h4 class="search-result-title">${escapeHtml(item.title)}</h4>
          ${item.description ? `<p class="search-result-desc">${escapeHtml(item.description)}</p>` : ''}
          ${extraInfo ? `<div class="search-result-meta">${extraInfo}</div>` : ''}
        </div>
        <div class="search-result-actions">
          <button class="search-extract-btn" title="Open in Extract tab">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
            Extract
          </button>
          ${item.download_url ? `
          <button class="search-download-btn" title="Download directly">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
          </button>` : ''}
        </div>`;

      card.querySelector('.search-extract-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        openInExtractTab(item.url);
      });

      const dlBtn = card.querySelector('.search-download-btn');
      if (dlBtn) {
        dlBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          if (item.source === 'youtube') {
            // Use yt-dlp download endpoint for YouTube
            const dlUrl = `/api/extract-download?url=${encodeURIComponent(item.download_url)}`;
            const filename = (item.title || 'download') + '.mp4';
            forceDownload(dlUrl, filename);
          } else {
            const filename = guessFilename(item.download_url, item.title);
            const proxyUrl = `/api/download?url=${encodeURIComponent(item.download_url)}&filename=${encodeURIComponent(filename)}`;
            forceDownload(proxyUrl, filename);
          }
        });
      }

      card.addEventListener('click', () => openInExtractTab(item.url));
      fragment.appendChild(card);
    });

    searchResults.appendChild(fragment);
  }

  function openInExtractTab(url) {
    urlInput.value = url;
    switchTab('extract');
    handleExtract();
  }

  function getTypeIcon(mediaType) {
    const icons = {
      documents: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
      video: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>',
      audio: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
      images: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
    };
    return icons[mediaType] || icons.documents;
  }

  function getSourceLabel(source) {
    const labels = { 'archive.org': 'Archive.org', 'openlibrary': 'OpenLibrary', 'youtube': 'YouTube', 'wikimedia': 'Wikimedia' };
    return labels[source] || source;
  }

  function buildExtraInfo(item) {
    const parts = [];
    if (item.extra?.duration) parts.push(formatDuration(item.extra.duration));
    if (item.extra?.views) parts.push(`${item.extra.views.toLocaleString()} views`);
    if (item.extra?.downloads) parts.push(`${item.extra.downloads.toLocaleString()} downloads`);
    if (item.extra?.pages) parts.push(`${item.extra.pages} pages`);
    if (item.date) { const yr = String(item.date).substring(0, 4); if (yr.match(/^\d{4}$/)) parts.push(yr); }
    return parts.join(' &middot; ');
  }

  // Search event bindings
  searchBtn.addEventListener('click', () => performSearch(false));
  searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') performSearch(false); });

  filterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      filterChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      searchState.type = chip.dataset.filter;
      if (searchInput.value.trim()) performSearch(false);
    });
  });

  loadMoreBtn.addEventListener('click', () => {
    if (!searchState.loading && searchState.hasMore) {
      searchState.page++;
      performSearch(true);
    }
  });

  // ---- Init ----
  renderHistory();

  // Focus input on load
  setTimeout(() => urlInput.focus(), 300);
});

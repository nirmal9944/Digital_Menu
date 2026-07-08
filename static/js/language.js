/**
 * language.js
 * ----------------------------------------------------------------------------
 * Universal language-switching controller shared by landing.html and menu.html.
 *
 * USAGE
 * ----------------------------------------------------------------------------
 *   <script src="language.js"></script>
 *   <script>
 *     const langManager = new LanguageManager();
 *     // Optional: use the official Cloud Translation API instead of the
 *     // free endpoint (more reliable batch translation, needs a key):
 *     // const langManager = new LanguageManager({ apiKey: 'YOUR_KEY' });
 *   </script>
 *
 * It auto-detects which markup is present on the page and wires up either
 * (or both) of:
 *   landing.html -> .lang-card #langDrop #dropBtn #dropList .lang-opt
 *   menu.html    -> #langWrap #langBtn #langMenu .lang-option
 *
 * CSS HOOKS this script expects you to style (none of this is invented DOM,
 * it only toggles classes/attributes on your existing markup):
 *   - #langDrop.open / #dropBtn[aria-expanded="true"]  -> show #dropList
 *   - #langWrap.open / #langBtn[aria-expanded="true"]  -> show #langMenu
 *   - body.lang-loading                                -> optional loading look
 *   - [aria-busy="true"] on #dropBtn / #langBtn         -> optional spinner state
 *
 * TRANSLATION BACKENDS
 *   1. Free/key-less: translate.googleapis.com ("client=gtx"). No setup
 *      required, but it's an unofficial endpoint with no SLA - Google can
 *      throttle or change it without notice. Fine for small UI vocabularies.
 *   2. Official: Cloud Translation API v2. Pass `apiKey` in the constructor
 *      options to use it instead - it does real batch translation (no
 *      delimiter-splitting tricks) and is the recommended path for
 *      production. Restrict the key by HTTP referrer in Google Cloud Console
 *      before shipping it in client-side code.
 *
 * BROWSER SUPPORT
 *   Written in modern ES6+ (classes, async/await, Map/WeakMap/Set,
 *   template literals). This will NOT run as-is in IE11 - there is no
 *   polyfill that adds async/await or class syntax support at runtime.
 *   To support IE11, run this file through Babel + a fetch/Promise polyfill
 *   as a build step; the logic itself has no IE11-specific blockers.
 * ----------------------------------------------------------------------------
 */

(function (global, document) {
  'use strict';

  if (!document) return;

  /* ============================================================
   * Configuration
   * ========================================================== */

  const STORAGE_LANG_KEY = 'site_lang_pref_v1';
  const STORAGE_CACHE_KEY = 'site_lang_cache_v1';
  const MAX_CACHE_ENTRIES = 800;

  const SUPPORTED_LANGS = ['en', 'ne', 'hi', 'zh', 'es'];

  const LANG_META = {
    en: { label: 'English', short: 'EN', flag: 'f-en', dir: 'ltr' },
    ne: { label: 'नेपाली (Nepali)', short: 'NE', flag: 'f-ne', dir: 'ltr' },
    hi: { label: 'हिन्दी (Hindi)', short: 'HI', flag: 'f-hi', dir: 'ltr' },
    zh: { label: '中文 (Chinese)', short: 'ZH', flag: 'f-zh', dir: 'ltr' },
    es: { label: 'Español (Spanish)', short: 'ES', flag: 'f-es', dir: 'ltr' }
  };

  // Free, key-less Google endpoint (client=gtx).
  const FREE_ENDPOINT = 'https://translate.googleapis.com/translate_a/single';
  // Official Cloud Translation v2 endpoint - used automatically when an
  // apiKey option is supplied.
  const CLOUD_ENDPOINT = 'https://translation.googleapis.com/language/translate/v2';

  const MAX_RETRIES = 2;
  const RETRY_BASE_DELAY = 500; // ms
  const REQUEST_TIMEOUT_MS = 8000;
  const CONCURRENCY_LIMIT = 4; // simultaneous free-API requests
  const SELECT_DEBOUNCE_MS = 220;
  const MUTATION_DEBOUNCE_MS = 150;

  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TEXTAREA', 'CODE', 'PRE']);

  /* ============================================================
   * Small utilities
   * ========================================================== */

  function debounce(fn, wait) {
    let t = null;
    return function debounced(...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function safeLocalStorageGet(key) {
    try { return global.localStorage.getItem(key); } catch (e) { return null; }
  }

  function safeLocalStorageSet(key, value) {
    try { global.localStorage.setItem(key, value); return true; }
    catch (e) { return false; }
  }

  function isElementVisible(el) {
    if (!el || !(el instanceof Element)) return true;
    const style = global.getComputedStyle ? global.getComputedStyle(el) : null;
    if (!style) return true;
    return style.display !== 'none' && style.visibility !== 'hidden';
  }

  function withTimeout(promise, ms) {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Request timed out')), ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // Tiny concurrency-limited task runner so a page full of text nodes
  // doesn't fire dozens of simultaneous requests at once.
  function runWithConcurrency(tasks, limit) {
    return new Promise((resolve) => {
      const results = new Array(tasks.length);
      let nextIndex = 0;
      let completed = 0;

      if (tasks.length === 0) { resolve(results); return; }

      function runNext() {
        if (nextIndex >= tasks.length) return;
        const current = nextIndex++;
        Promise.resolve()
          .then(() => tasks[current]())
          .then((value) => { results[current] = { ok: true, value }; })
          .catch((err) => { results[current] = { ok: false, error: err }; })
          .finally(() => {
            completed++;
            if (completed === tasks.length) { resolve(results); return; }
            runNext();
          });
      }

      const starters = Math.min(limit, tasks.length);
      for (let i = 0; i < starters; i++) runNext();
    });
  }

  /* ============================================================
   * Minimal, self-contained toast for user-facing error messages.
   * No external CSS/markup dependency - it builds and styles itself.
   * ========================================================== */

  function ensureToastHost() {
    let host = document.getElementById('langjs-toast-host');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'langjs-toast-host';
    Object.assign(host.style, {
      position: 'fixed', zIndex: '99999', right: '16px', bottom: '16px',
      display: 'flex', flexDirection: 'column', gap: '8px', pointerEvents: 'none'
    });
    document.body.appendChild(host);
    return host;
  }

  function showToast(message, type) {
    try {
      const host = ensureToastHost();
      const note = document.createElement('div');
      note.setAttribute('role', 'status');
      note.setAttribute('aria-live', 'polite');
      note.textContent = message;
      Object.assign(note.style, {
        pointerEvents: 'auto', maxWidth: '320px', padding: '10px 14px',
        borderRadius: '8px', fontSize: '13px', lineHeight: '1.4',
        fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
        color: '#fff', boxShadow: '0 4px 14px rgba(0,0,0,0.18)',
        background: type === 'error' ? '#c0392b' : '#2d3436',
        opacity: '0', transform: 'translateY(8px)',
        transition: 'opacity .25s ease, transform .25s ease'
      });
      host.appendChild(note);
      requestAnimationFrame(() => {
        note.style.opacity = '1';
        note.style.transform = 'translateY(0)';
      });
      setTimeout(() => {
        note.style.opacity = '0';
        note.style.transform = 'translateY(8px)';
        setTimeout(() => note.remove(), 300);
      }, 4200);
    } catch (e) {
      console.warn('[language.js]', message);
    }
  }

  /* ============================================================
   * LanguageManager
   * ========================================================== */

  class LanguageManager {
    constructor(options = {}) {
      this.options = Object.assign({
        apiKey: null,           // optional Cloud Translation API key
        debug: false,
        translateOnInit: true   // auto-translate on load if a non-English pref is stored
      }, options);

      this.supportedLangs = SUPPORTED_LANGS.slice();
      this.langMeta = LANG_META;

      this.cache = new Map();            // `${lang}::${text}` -> translated text
      this.originalText = new WeakMap(); // text Node -> original (English) text
      this.changeListeners = [];         // subscribers via onLanguageChange()

      this.currentLang = 'en';
      this.requestGeneration = 0; // bumped to invalidate stale in-flight translations
      this.isApplying = false;    // guards the MutationObserver against our own writes
      this.observer = null;
      this.hasLandingUI = false;
      this.hasMenuUI = false;

      this._debouncedSelect = debounce(this._applySelection.bind(this), SELECT_DEBOUNCE_MS);

      this.init();
    }

    /* ---------------- bootstrap ---------------- */

    init() {
      this._loadCacheFromStorage();
      this.currentLang = this.loadUserPreference() || this.getBrowserLanguage();

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => this._initDomBound());
      } else {
        this._initDomBound();
      }
    }

    _initDomBound() {
      this.setPageAttributes(this.currentLang);
      this.detectPageRegions();
      this.setupEventListeners();
      this.setupCrossPageSync();
      this.setupMutationObserver();
      this.updateUI(this.currentLang);

      if (this.options.translateOnInit && this.currentLang !== 'en') {
        this.translatePage(this.currentLang, { silent: true });
      }
    }

    detectPageRegions() {
      this.hasLandingUI = !!document.querySelector('.lang-card');
      this.hasMenuUI = !!document.querySelector('#langWrap');
    }

    /* ---------------- preference / detection ---------------- */

    getBrowserLanguage() {
      const nav = global.navigator || {};
      const raw = (nav.language || (nav.languages && nav.languages[0]) || 'en').toLowerCase();
      const short = raw.split('-')[0];
      return this.supportedLangs.includes(short) ? short : 'en';
    }

    loadUserPreference() {
      const stored = safeLocalStorageGet(STORAGE_LANG_KEY);
      return stored && this.supportedLangs.includes(stored) ? stored : null;
    }

    saveUserPreference(lang) {
      safeLocalStorageSet(STORAGE_LANG_KEY, lang);
    }

    setPageAttributes(lang) {
      const meta = this.langMeta[lang] || this.langMeta.en;
      document.documentElement.setAttribute('lang', lang);
      document.documentElement.setAttribute('dir', meta.dir || 'ltr');
    }

    /* ---------------- translation cache ---------------- */

    _loadCacheFromStorage() {
      const raw = safeLocalStorageGet(STORAGE_CACHE_KEY);
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw);
        Object.keys(parsed).forEach((key) => this.cache.set(key, parsed[key]));
      } catch (e) {
        if (this.options.debug) console.warn('[language.js] cache parse failed', e);
      }
    }

    _persistCache() {
      try {
        const entries = Array.from(this.cache.entries());
        const slice = entries.slice(Math.max(0, entries.length - MAX_CACHE_ENTRIES));
        const obj = {};
        slice.forEach(([k, v]) => { obj[k] = v; });
        safeLocalStorageSet(STORAGE_CACHE_KEY, JSON.stringify(obj));
      } catch (e) {
        if (this.options.debug) console.warn('[language.js] cache persist failed', e);
      }
    }

    /* ---------------- event wiring (delegated) ---------------- */

    setupEventListeners() {
      document.addEventListener('click', (e) => {
        const langOpt = e.target.closest('.lang-opt, .lang-option');
        if (langOpt) {
          e.preventDefault();
          const lang = langOpt.getAttribute('data-lang');
          if (lang) this.handleLanguageSelect(lang);
          return;
        }

        if (e.target.closest('#dropBtn')) {
          e.preventDefault();
          this._toggleDropdown();
          return;
        }

        if (e.target.closest('#langBtn')) {
          e.preventDefault();
          this._toggleMenu();
          return;
        }

        // Click outside an open panel closes it.
        if (!e.target.closest('#langDrop')) this._closeDropdown();
        if (!e.target.closest('#langWrap')) this._closeMenu();
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          this._closeDropdown();
          this._closeMenu();
          return;
        }
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        const container = e.target.closest('#dropList, #langMenu');
        if (!container) return;
        e.preventDefault();
        const items = Array.from(container.querySelectorAll('.lang-opt, .lang-option'));
        const idx = items.indexOf(document.activeElement);
        let nextIdx = idx;
        if (e.key === 'ArrowDown') nextIdx = idx < 0 ? 0 : (idx + 1) % items.length;
        if (e.key === 'ArrowUp') nextIdx = idx < 0 ? items.length - 1 : (idx - 1 + items.length) % items.length;
        if (items[nextIdx]) items[nextIdx].focus();
      });
    }

    _toggleDropdown() {
      const btn = document.getElementById('dropBtn');
      if (!btn) return;
      const isOpen = btn.getAttribute('aria-expanded') === 'true';
      if (isOpen) this._closeDropdown(); else this._openDropdown();
    }

    _openDropdown() {
      const btn = document.getElementById('dropBtn');
      const wrap = document.getElementById('langDrop');
      const list = document.getElementById('dropList');
      if (btn) btn.setAttribute('aria-expanded', 'true');
      if (wrap) wrap.classList.add('open');
      const focusTarget = list && (list.querySelector('.lang-opt.sel') || list.querySelector('.lang-opt'));
      if (focusTarget) focusTarget.focus();
    }

    _closeDropdown() {
      const btn = document.getElementById('dropBtn');
      const wrap = document.getElementById('langDrop');
      if (btn) btn.setAttribute('aria-expanded', 'false');
      if (wrap) wrap.classList.remove('open');
    }

    _toggleMenu() {
      const btn = document.getElementById('langBtn');
      if (!btn) return;
      const isOpen = btn.getAttribute('aria-expanded') === 'true';
      if (isOpen) this._closeMenu(); else this._openMenu();
    }

    _openMenu() {
      const btn = document.getElementById('langBtn');
      const wrap = document.getElementById('langWrap');
      const menu = document.getElementById('langMenu');
      if (btn) btn.setAttribute('aria-expanded', 'true');
      if (wrap) wrap.classList.add('open');
      const focusTarget = menu && (menu.querySelector('.lang-option.active') || menu.querySelector('.lang-option'));
      if (focusTarget) focusTarget.focus();
    }

    _closeMenu() {
      const btn = document.getElementById('langBtn');
      const wrap = document.getElementById('langWrap');
      if (btn) btn.setAttribute('aria-expanded', 'false');
      if (wrap) wrap.classList.remove('open');
    }

    /* ---------------- selection handling ---------------- */

    handleLanguageSelect(lang) {
      if (!this.supportedLangs.includes(lang)) return;
      // Reflect the choice immediately (selected state, flag, label) even
      // though the actual translation request is debounced below - this
      // keeps rapid re-clicks cheap while the UI still feels instant.
      this.updateUI(lang);
      this._closeDropdown();
      this._closeMenu();
      this._debouncedSelect(lang);
    }

    _applySelection(lang) {
      if (lang === this.currentLang) return;
      this.currentLang = lang;
      this.saveUserPreference(lang);
      this.setPageAttributes(lang);
      this.translatePage(lang);
    }

    /* ---------------- UI updates ---------------- */

    updateUI(lang) {
      const meta = this.langMeta[lang] || this.langMeta.en;
      if (this.hasLandingUI) this.updateLandingUI(lang, meta);
      if (this.hasMenuUI) this.updateMenuUI(lang, meta);
    }

    updateLandingUI(lang, meta) {
      meta = meta || this.langMeta[lang] || this.langMeta.en;
      const dropBtn = document.getElementById('dropBtn');
      const dtLabel = document.getElementById('dtLabel');
      const flagEl = dropBtn ? dropBtn.querySelector('.dt-flag') : null;

      if (dtLabel) dtLabel.textContent = meta.label;
      if (dropBtn) dropBtn.setAttribute('aria-label', `Select language, currently ${meta.label}`);
      if (flagEl) {
        Array.from(flagEl.classList).filter((c) => /^f-/.test(c)).forEach((c) => flagEl.classList.remove(c));
        flagEl.classList.add(meta.flag);
      }

      document.querySelectorAll('.lang-opt').forEach((opt) => {
        const isMatch = opt.getAttribute('data-lang') === lang;
        opt.classList.toggle('sel', isMatch);
        opt.setAttribute('aria-selected', String(isMatch));
      });
    }

    updateMenuUI(lang, meta) {
      meta = meta || this.langMeta[lang] || this.langMeta.en;
      const label = document.getElementById('langLabel');
      if (label) label.textContent = meta.short;

      document.querySelectorAll('.lang-option').forEach((opt) => {
        const isMatch = opt.getAttribute('data-lang') === lang;
        opt.classList.toggle('active', isMatch);
        if (isMatch) opt.setAttribute('aria-current', 'true');
        else opt.removeAttribute('aria-current');
      });
    }

    /* ---------------- text-node discovery ---------------- */

    getTextNodes(root) {
      root = root || document.body;
      const nodes = [];
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: (node) => (this.shouldTranslate(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP)
      });
      let current;
      while ((current = walker.nextNode())) nodes.push(current);
      return nodes;
    }

    shouldTranslate(node) {
      if (!node || node.nodeType !== Node.TEXT_NODE) return false;
      const text = node.textContent;
      if (!text || !text.trim()) return false;
      // Skip strings with no letters at all (pure numbers/punctuation/icons).
      if (!/[a-zA-Z\u00C0-\u024F\u0900-\u097F\u4e00-\u9fff]/.test(text)) return false;

      const parent = node.parentElement;
      if (!parent) return false;
      if (SKIP_TAGS.has(parent.tagName)) return false;
      if (parent.closest('[data-no-translate]')) return false;
      if (parent.isContentEditable) return false;
      return true;
    }

    /* ---------------- page-level translation ---------------- */

    async translatePage(targetLang, opts = {}) {
      const generation = ++this.requestGeneration;
      const silent = !!opts.silent;

      if (targetLang === 'en') {
        this._restoreOriginal();
        this.updateUI('en');
        this._notifyChangeListeners('en');
        return;
      }

      const nodes = this.getTextNodes(document.body).filter((n) => isElementVisible(n.parentElement));

      nodes.forEach((node) => {
        if (!this.originalText.has(node)) this.originalText.set(node, node.textContent);
      });

      const uniqueTexts = Array.from(new Set(
        nodes.map((n) => this.originalText.get(n).trim()).filter(Boolean)
      ));
      const needed = uniqueTexts.filter((t) => !this.cache.has(`${targetLang}::${t}`));

      this._setLoading(true);

      const isOffline = global.navigator && global.navigator.onLine === false;
      if (isOffline) {
        if (!silent) showToast("You're offline - showing cached translations where available.", 'error');
      } else if (needed.length) {
        try {
          const translations = await this.translateBatch(needed, targetLang);
          translations.forEach((translated, i) => {
            if (translated) this.cache.set(`${targetLang}::${needed[i]}`, translated);
          });
          this._persistCache();
        } catch (err) {
          if (this.options.debug) console.error('[language.js] translation failed', err);
          if (!silent) showToast('Translation is temporarily unavailable. Showing English instead.', 'error');
          this._setLoading(false);
          if (generation === this.requestGeneration) {
            this.currentLang = 'en';
            this.setPageAttributes('en');
            this._restoreOriginal();
            this.updateUI('en');
            this._notifyChangeListeners('en');
          }
          return;
        }
      }

      // A newer selection superseded this one while we were waiting - drop it.
      if (generation !== this.requestGeneration) { this._setLoading(false); return; }

      this._applyTranslationsToDOM(nodes, targetLang);
      this.updateUI(targetLang);
      this._setLoading(false);
      document.dispatchEvent(new CustomEvent('languagechange', { detail: { lang: targetLang } }));
      this._notifyChangeListeners(targetLang);
    }

    async translateElement(element, targetLang) {
      targetLang = targetLang || this.currentLang;
      if (!element || targetLang === 'en') return;

      const nodes = this.getTextNodes(element);
      nodes.forEach((node) => {
        if (!this.originalText.has(node)) this.originalText.set(node, node.textContent);
      });

      const uniqueTexts = Array.from(new Set(
        nodes.map((n) => this.originalText.get(n).trim()).filter(Boolean)
      ));
      const needed = uniqueTexts.filter((t) => !this.cache.has(`${targetLang}::${t}`));

      if (needed.length) {
        try {
          const translations = await this.translateBatch(needed, targetLang);
          translations.forEach((tr, i) => { if (tr) this.cache.set(`${targetLang}::${needed[i]}`, tr); });
          this._persistCache();
        } catch (e) {
          if (this.options.debug) console.warn('[language.js] dynamic content translation failed', e);
          return; // leave new content in its original language rather than break the page
        }
      }

      this._applyTranslationsToDOM(nodes, targetLang);
    }

    _applyTranslationsToDOM(nodes, targetLang) {
      this.isApplying = true;
      nodes.forEach((node) => {
        const original = this.originalText.get(node);
        if (!original) return;
        const translated = this.cache.get(`${targetLang}::${original.trim()}`);
        if (translated) {
          const leading = original.match(/^\s*/)[0];
          const trailing = original.match(/\s*$/)[0];
          node.textContent = `${leading}${translated}${trailing}`;
        }
      });
      this.isApplying = false;
    }

    _restoreOriginal() {
      this.isApplying = true;
      this.getTextNodes(document.body).forEach((node) => {
        if (this.originalText.has(node)) node.textContent = this.originalText.get(node);
      });
      this.isApplying = false;
    }

    _setLoading(isLoading) {
      document.body.classList.toggle('lang-loading', isLoading);
      ['langBtn', 'dropBtn'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.setAttribute('aria-busy', String(isLoading));
      });
      document.dispatchEvent(new CustomEvent(isLoading ? 'languagechange:start' : 'languagechange:end'));
    }

    /* ---------------- backend calls ---------------- */

    translateBatch(texts, targetLang) {
      return this.options.apiKey
        ? this._translateBatchCloud(texts, targetLang)
        : this._translateBatchFree(texts, targetLang);
    }

    async _translateBatchCloud(texts, targetLang) {
      const url = `${CLOUD_ENDPOINT}?key=${encodeURIComponent(this.options.apiKey)}`;
      const res = await this._fetchWithRetry(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q: texts, target: targetLang, format: 'text' })
      });
      const json = await res.json();
      const translations = (json && json.data && json.data.translations) || [];
      return translations.map((t) => this._decodeHTMLEntities(t.translatedText));
    }

    async _translateBatchFree(texts, targetLang) {
      const tasks = texts.map((text) => () => this.translateText(text, targetLang));
      const results = await runWithConcurrency(tasks, CONCURRENCY_LIMIT);
      if (this.options.debug) {
        results.forEach((r, i) => { if (!r.ok) console.warn('[language.js] failed to translate:', texts[i], r.error); });
      }
      return results.map((r) => (r.ok ? r.value : ''));
    }

    async translateText(text, targetLang) {
      const cacheKey = `${targetLang}::${text}`;
      if (this.cache.has(cacheKey)) return this.cache.get(cacheKey);

      const params = new URLSearchParams({ client: 'gtx', sl: 'auto', tl: targetLang, dt: 't', q: text });
      const url = `${FREE_ENDPOINT}?${params.toString()}`;

      const res = await this._fetchWithRetry(url, { method: 'GET' });
      const json = await res.json();
      // Response shape: [[["translated","original",null,null,3], ...], null, "en", ...]
      const segments = (json && json[0]) || [];
      const translated = segments.map((seg) => (seg && seg[0]) || '').join('');
      return translated || text;
    }

    async _fetchWithRetry(url, init, attempt = 0) {
      try {
        const res = await withTimeout(fetch(url, init), REQUEST_TIMEOUT_MS);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res;
      } catch (err) {
        if (attempt < MAX_RETRIES) {
          await sleep(RETRY_BASE_DELAY * (attempt + 1));
          return this._fetchWithRetry(url, init, attempt + 1);
        }
        throw err;
      }
    }

    _decodeHTMLEntities(str) {
      const ta = document.createElement('textarea');
      ta.innerHTML = str;
      return ta.value;
    }

    /* ---------------- dynamic content (MutationObserver) ---------------- */

    setupMutationObserver() {
      if (!('MutationObserver' in global)) return;
      this.observer = new MutationObserver((mutations) => {
        if (this.isApplying || this.currentLang === 'en') return;

        const toTranslate = new Set();
        mutations.forEach((m) => {
          m.addedNodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE) toTranslate.add(node);
            else if (node.nodeType === Node.TEXT_NODE && node.parentElement) toTranslate.add(node.parentElement);
          });
        });
        if (toTranslate.size === 0) return;

        clearTimeout(this._mutationDebounceTimer);
        this._mutationDebounceTimer = setTimeout(() => {
          toTranslate.forEach((el) => this.translateElement(el, this.currentLang));
        }, MUTATION_DEBOUNCE_MS);
      });

      this.observer.observe(document.body, { childList: true, subtree: true });
    }

    /* ---------------- cross-page / cross-tab sync ---------------- */

    setupCrossPageSync() {
      global.addEventListener('storage', (e) => {
        if (e.key !== STORAGE_LANG_KEY || !e.newValue) return;
        if (e.newValue === this.currentLang || !this.supportedLangs.includes(e.newValue)) return;
        this.currentLang = e.newValue;
        this.setPageAttributes(e.newValue);
        this.updateUI(e.newValue);
        this.translatePage(e.newValue, { silent: true });
      });

      // Covers back/forward-cache restores, where this script's top-level
      // code doesn't re-run but the stored preference may have changed.
      global.addEventListener('pageshow', (e) => {
        if (!e.persisted) return;
        const stored = this.loadUserPreference();
        if (stored && stored !== this.currentLang) {
          this.currentLang = stored;
          this.setPageAttributes(stored);
          this.updateUI(stored);
          this.translatePage(stored, { silent: true });
        }
      });
    }

    /* ---------------- subscriptions ---------------- */

    onLanguageChange(callback) {
      if (typeof callback !== 'function') return () => {};
      this.changeListeners.push(callback);
      return () => { this.changeListeners = this.changeListeners.filter((cb) => cb !== callback); };
    }

    _notifyChangeListeners(lang) {
      this.changeListeners.forEach((cb) => {
        try { cb(lang); } catch (e) { if (this.options.debug) console.warn('[language.js] listener error', e); }
      });
    }
  }

  global.LanguageManager = LanguageManager;

})(typeof window !== 'undefined' ? window : this, typeof document !== 'undefined' ? document : null);
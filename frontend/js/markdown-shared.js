(function(global) {
    const ABSOLUTE_FILE_PATH_PATTERN = /((?:\/[^\s<>"'`)\]）}]+)+\.[A-Za-z0-9_-]+(?::\d+(?::\d+)?)?)/g;
    const LOCAL_PREVIEW_ROUTE_PATTERN = /^\/local-file-(?:view|raw)(?:[/?#]|$)/;
    const LOCAL_PREVIEW_OVERLAY_ID = 'cc-local-file-preview-overlay';

    function isAbsoluteLocalReference(rawValue) {
        const raw = String(rawValue || '').trim();
        if (!raw) return false;
        let candidate = raw;
        if (candidate.startsWith('file://')) {
            candidate = candidate.replace(/^file:\/\//i, '');
        }
        const lineColMatch = candidate.match(/^(.*?):(\d+)(?::(\d+))?$/);
        if (lineColMatch) {
            candidate = lineColMatch[1];
        }
        const hashIndex = candidate.indexOf('#');
        if (hashIndex >= 0) {
            candidate = candidate.slice(0, hashIndex);
        }
        const normalizedPath = candidate.replace(/\\/g, '/');
        return normalizedPath.startsWith('/') || /^[A-Za-z]:\//.test(normalizedPath);
    }

    function parseLocalReference(rawValue) {
        const raw = String(rawValue || '').trim();
        if (!raw) return null;

        let candidate = raw;
        if (candidate.startsWith('file://')) {
            candidate = candidate.replace(/^file:\/\//i, '');
        }

        let hash = '';
        const hashIndex = candidate.indexOf('#');
        if (hashIndex >= 0) {
            hash = candidate.slice(hashIndex + 1);
            candidate = candidate.slice(0, hashIndex);
        }

        const lineColMatch = candidate.match(/^(.*?):(\d+)(?::(\d+))?$/);
        let path = candidate;
        let line = '';
        let col = '';
        if (lineColMatch) {
            path = lineColMatch[1];
            line = lineColMatch[2] || '';
            col = lineColMatch[3] || '';
        }

        if (!path) return null;
        const normalizedPath = path.replace(/\\/g, '/');
        if (/^[a-z][a-z0-9+.-]*:/i.test(normalizedPath) && !/^file:/i.test(normalizedPath)) {
            return null;
        }
        const looksAbsolute = normalizedPath.startsWith('/') || /^[A-Za-z]:\//.test(normalizedPath);
        const looksRelativeFile = /^[A-Za-z0-9._-]/.test(normalizedPath) && /[/.]/.test(normalizedPath);
        if (!looksAbsolute && !looksRelativeFile) return null;

        if (hash && !line) {
            const hashMatch = hash.match(/^L(\d+)(?:C(\d+))?$/i);
            if (hashMatch) {
                line = hashMatch[1] || '';
                col = hashMatch[2] || '';
            }
        }

        return { path, line, col };
    }

    function buildLocalFilePreviewUrl(rawValue) {
        const raw = String(rawValue || '').trim();
        if (!raw || LOCAL_PREVIEW_ROUTE_PATTERN.test(raw)) return null;
        const parsed = parseLocalReference(rawValue);
        if (!parsed) return null;
        const params = new URLSearchParams();
        params.set('path', parsed.path);
        if (parsed.line) params.set('line', parsed.line);
        if (parsed.col) params.set('col', parsed.col);
        return `/local-file-view?${params.toString()}`;
    }

    function rewriteLocalFileLinks(root) {
        if (!root) return;
        root.querySelectorAll('a[href]').forEach((anchor) => {
            if (anchor.dataset.localFileLink === '1') return;
            const rawHref = anchor.getAttribute('href') || '';
            if (LOCAL_PREVIEW_ROUTE_PATTERN.test(rawHref)) {
                anchor.dataset.localFileLink = '1';
                anchor.classList.add('cc-local-file-link');
                return;
            }
            const previewUrl = buildLocalFilePreviewUrl(rawHref);
            if (!previewUrl) return;
            anchor.setAttribute('href', previewUrl);
            anchor.setAttribute('target', '_blank');
            anchor.setAttribute('rel', 'noopener noreferrer');
            anchor.dataset.localFileLink = '1';
            anchor.classList.add('cc-local-file-link');
        });
    }

    function renderCodeBlockPathLines(codeElement) {
        if (!codeElement || codeElement.dataset.localFileCodeBlock === '1') return false;
        const rawText = String(codeElement.textContent || '').replace(/\r\n/g, '\n');
        if (!rawText.trim()) return false;

        const lines = rawText.split('\n');
        let foundPath = false;
        const fragment = document.createDocumentFragment();

        lines.forEach((line, index) => {
            const trimmed = line.trim();
            if (!trimmed) {
                fragment.appendChild(document.createTextNode(''));
            } else {
                const previewUrl = isAbsoluteLocalReference(trimmed) ? buildLocalFilePreviewUrl(trimmed) : null;
                if (previewUrl) {
                    foundPath = true;
                    const anchor = document.createElement('a');
                    anchor.href = previewUrl;
                    anchor.target = '_blank';
                    anchor.rel = 'noopener noreferrer';
                    anchor.dataset.localFileLink = '1';
                    anchor.className = 'cc-local-file-link cc-local-file-link-block';
                    anchor.textContent = trimmed;
                    fragment.appendChild(anchor);
                } else {
                    fragment.appendChild(document.createTextNode(line));
                }
            }

            if (index < lines.length - 1) {
                fragment.appendChild(document.createElement('br'));
            }
        });

        if (!foundPath) return false;
        codeElement.innerHTML = '';
        codeElement.appendChild(fragment);
        codeElement.dataset.localFileCodeBlock = '1';
        codeElement.parentElement && codeElement.parentElement.classList.add('cc-local-file-code-block');
        return true;
    }

    function linkifyLocalFileCodeBlocks(root) {
        if (!root || typeof document === 'undefined') return;
        root.querySelectorAll('pre code, code').forEach((codeElement) => {
            renderCodeBlockPathLines(codeElement);
        });
    }

    function ensureLocalPreviewOverlay() {
        if (typeof document === 'undefined') return null;
        let overlay = document.getElementById(LOCAL_PREVIEW_OVERLAY_ID);
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.id = LOCAL_PREVIEW_OVERLAY_ID;
        overlay.className = 'cc-local-file-preview-overlay';
        overlay.innerHTML = [
            '<div class="cc-local-file-preview-backdrop" data-close="1"></div>',
            '<div class="cc-local-file-preview-panel" role="dialog" aria-modal="true" aria-label="Local file preview">',
            '  <div class="cc-local-file-preview-header">',
            '    <div class="cc-local-file-preview-title">文件预览</div>',
            '    <button type="button" class="cc-local-file-preview-close" aria-label="Close preview" data-close="1">✕</button>',
            '  </div>',
            '  <iframe class="cc-local-file-preview-frame" title="Local file preview"></iframe>',
            ' </div>'
        ].join('');
        document.body.appendChild(overlay);

        overlay.addEventListener('click', (event) => {
            const target = event.target;
            if (target && target.getAttribute && target.getAttribute('data-close') === '1') {
                closeLocalPreviewOverlay();
            }
        });

        return overlay;
    }

    function openLocalPreviewOverlay(url, title) {
        const overlay = ensureLocalPreviewOverlay();
        if (!overlay) return false;
        const frame = overlay.querySelector('.cc-local-file-preview-frame');
        const titleEl = overlay.querySelector('.cc-local-file-preview-title');
        if (!frame || !titleEl) return false;
        frame.src = String(url || '');
        titleEl.textContent = title || '文件预览';
        overlay.classList.add('show');
        document.body.classList.add('cc-local-file-preview-open');
        return true;
    }

    function closeLocalPreviewOverlay() {
        if (typeof document === 'undefined') return;
        const overlay = document.getElementById(LOCAL_PREVIEW_OVERLAY_ID);
        if (!overlay) return;
        const frame = overlay.querySelector('.cc-local-file-preview-frame');
        overlay.classList.remove('show');
        document.body.classList.remove('cc-local-file-preview-open');
        if (frame) {
            setTimeout(() => {
                if (!overlay.classList.contains('show')) frame.src = 'about:blank';
            }, 120);
        }
    }

    function bindLocalPreviewClicks() {
        if (typeof document === 'undefined' || document.__ccLocalPreviewBound) return;
        document.__ccLocalPreviewBound = true;
        document.addEventListener('click', (event) => {
            const anchor = event.target && event.target.closest ? event.target.closest('a.cc-local-file-link') : null;
            if (!anchor) return;
            const href = anchor.getAttribute('href') || '';
            if (!LOCAL_PREVIEW_ROUTE_PATTERN.test(href)) return;
            event.preventDefault();
            const title = anchor.textContent ? String(anchor.textContent).trim() : '文件预览';
            openLocalPreviewOverlay(href, title);
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closeLocalPreviewOverlay();
        });
    }

    function linkifyLocalFileText(root) {
        if (!root || typeof document === 'undefined') return;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                const parent = node && node.parentElement;
                if (!parent) return NodeFilter.FILTER_REJECT;
                if (parent.closest('a, code, pre, script, style, textarea')) {
                    return NodeFilter.FILTER_REJECT;
                }
                const text = String(node.nodeValue || '');
                ABSOLUTE_FILE_PATH_PATTERN.lastIndex = 0;
                return ABSOLUTE_FILE_PATH_PATTERN.test(text)
                    ? NodeFilter.FILTER_ACCEPT
                    : NodeFilter.FILTER_REJECT;
            }
        });

        const textNodes = [];
        let current = walker.nextNode();
        while (current) {
            textNodes.push(current);
            current = walker.nextNode();
        }

        textNodes.forEach((node) => {
            const text = String(node.nodeValue || '');
            ABSOLUTE_FILE_PATH_PATTERN.lastIndex = 0;
            let match;
            let lastIndex = 0;
            let replaced = false;
            const fragment = document.createDocumentFragment();

            while ((match = ABSOLUTE_FILE_PATH_PATTERN.exec(text)) !== null) {
                const pathText = match[1];
                const matchStart = match.index;

                if (matchStart > lastIndex) {
                    fragment.appendChild(document.createTextNode(text.slice(lastIndex, matchStart)));
                }

                const previewUrl = buildLocalFilePreviewUrl(pathText);
                if (previewUrl) {
                    const anchor = document.createElement('a');
                    anchor.href = previewUrl;
                    anchor.target = '_blank';
                    anchor.rel = 'noopener noreferrer';
                    anchor.dataset.localFileLink = '1';
                    anchor.className = 'cc-local-file-link';
                    anchor.textContent = pathText;
                    fragment.appendChild(anchor);
                    replaced = true;
                } else {
                    fragment.appendChild(document.createTextNode(pathText));
                }

                lastIndex = matchStart + pathText.length;
            }

            if (!replaced) return;
            if (lastIndex < text.length) {
                fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
            }
            node.parentNode.replaceChild(fragment, node);
        });
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * API / LLM 有时把换行打成字面量反斜杠+n（两个字符），界面会原样显示「\n」。
     * 在 Markdown 或纯文本展示前转成真实换行（不处理已含真实换行的正常字符串）。
     */
    function normalizeEscapedNewlines(s) {
        const str = s == null ? '' : String(s);
        if (str.indexOf('\\') === -1) return str;
        return str
            .replace(/\\r\\n/g, '\n')
            .replace(/\\n/g, '\n')
            .replace(/\\r/g, '\n')
            .replace(/\\t/g, '\t');
    }

    function configureMarked() {
        if (typeof global.marked === 'undefined') return;
        if (global.marked.__clawcrossConfigured) return;

        global.marked.setOptions({
            gfm: true,
            // LLM 常用「单行换行」分段；开启后单 \n 转为 <br>，移动端可读性更好
            breaks: true,
            highlight(code, lang) {
                if (typeof global.hljs === 'undefined') {
                    return escapeHtml(code);
                }
                const language = lang && global.hljs.getLanguage(lang) ? lang : 'plaintext';
                return global.hljs.highlight(code, { language }).value;
            },
            langPrefix: 'hljs language-'
        });

        global.marked.__clawcrossConfigured = true;
    }

    function render(content) {
        const raw = normalizeEscapedNewlines(content == null ? '' : String(content));
        configureMarked();
        if (!raw) return '';
        if (typeof global.marked === 'undefined') {
            return escapeHtml(raw).replace(/\n/g, '<br>');
        }
        return global.marked.parse(raw);
    }

    function highlight(root) {
        linkifyLocalFileText(root);
        linkifyLocalFileCodeBlocks(root);
        rewriteLocalFileLinks(root);
        if (!root || typeof global.hljs === 'undefined') return;
        root.querySelectorAll('pre code').forEach((block) => {
            if (block.dataset.localFileCodeBlock === '1') return;
            try {
                global.hljs.highlightElement(block);
            } catch (_) {
                // Ignore malformed snippets and keep rendering moving.
            }
        });
    }

    function renderInto(element, content) {
        if (!element) return;
        element.classList.add('markdown-body', 'tc-markdown');
        element.innerHTML = render(content);
        highlight(element);
    }

    global.ClawcrossMarkdown = {
        buildLocalFilePreviewUrl,
        closeLocalPreviewOverlay,
        configure: configureMarked,
        escapeHtml,
        linkifyLocalFileText,
        linkifyLocalFileCodeBlocks,
        normalizeEscapedNewlines,
        openLocalPreviewOverlay,
        parseLocalReference,
        render,
        highlight,
        renderInto,
        rewriteLocalFileLinks,
    };

    configureMarked();
    bindLocalPreviewClicks();
})(window);

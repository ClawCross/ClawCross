(function (root, factory) {
    const api = factory(root.ClawcrossPretext || null);
    root.ClawcrossTextLayout = api;
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function (pretext) {
    const HUGE_WIDTH = 4096;
    const preparedCache = new Map();
    const plainWidthCache = new Map();
    const segmenter = typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function'
        ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
        : null;

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function normalizeText(value, whiteSpace) {
        const text = String(value == null ? '' : value);
        if (whiteSpace === 'pre-wrap') return text;
        return text.replace(/\s+/g, ' ').trim();
    }

    function splitGraphemes(value) {
        const text = String(value == null ? '' : value);
        if (!text) return [];
        if (segmenter) {
            return Array.from(segmenter.segment(text), function (part) { return part.segment; });
        }
        return Array.from(text);
    }

    function getFont(value) {
        return String(value || '12px Arial');
    }

    function getPreparedKey(text, font, whiteSpace, segmented) {
        return [segmented ? 'segments' : 'layout', font, whiteSpace || 'normal', text].join('::');
    }

    function getPrepared(text, font, whiteSpace, segmented) {
        if (!pretext || typeof pretext.prepareWithSegments !== 'function' || typeof pretext.layoutWithLines !== 'function') {
            return null;
        }
        const key = getPreparedKey(text, font, whiteSpace, segmented);
        if (preparedCache.has(key)) return preparedCache.get(key);
        const prepared = segmented
            ? pretext.prepareWithSegments(text, font, { whiteSpace: whiteSpace || 'normal' })
            : pretext.prepare(text, font, { whiteSpace: whiteSpace || 'normal' });
        preparedCache.set(key, prepared);
        return prepared;
    }

    function getCanvasContext() {
        if (typeof document === 'undefined' || !document.createElement) return null;
        if (!root.__clawcrossTextMeasureCanvas) {
            root.__clawcrossTextMeasureCanvas = document.createElement('canvas');
        }
        return root.__clawcrossTextMeasureCanvas.getContext('2d');
    }

    function heuristicCharWidth(ch) {
        if (!ch) return 0;
        if (/\s/.test(ch)) return 0.34;
        if (/[\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]/.test(ch)) return 1.68;
        if (/[A-Z]/.test(ch)) return 0.92;
        if (/[a-z0-9]/.test(ch)) return 0.78;
        return 1.02;
    }

    function measurePlainText(text, font) {
        const normalizedFont = getFont(font);
        const cacheKey = normalizedFont + '::' + text;
        if (plainWidthCache.has(cacheKey)) return plainWidthCache.get(cacheKey);
        const ctx = getCanvasContext();
        let width = 0;
        if (ctx) {
            ctx.font = normalizedFont;
            width = ctx.measureText(text).width;
        } else {
            const fontSizeMatch = normalizedFont.match(/(\d+(?:\.\d+)?)px/);
            const baseSize = fontSizeMatch ? Number(fontSizeMatch[1]) : 12;
            width = splitGraphemes(text).reduce(function (sum, ch) {
                return sum + heuristicCharWidth(ch) * baseSize;
            }, 0);
        }
        plainWidthCache.set(cacheKey, width);
        return width;
    }

    function fallbackLayout(text, font, maxWidth, lineHeight, whiteSpace) {
        const normalized = normalizeText(text, whiteSpace);
        if (!normalized) {
            return { lines: [''], lineCount: 1, width: 0, height: lineHeight };
        }
        const limit = Number.isFinite(maxWidth) ? Math.max(maxWidth, 1) : HUGE_WIDTH;
        const lines = [];
        let current = '';

        splitGraphemes(normalized).forEach(function (grapheme) {
            const next = current + grapheme;
            if (current && measurePlainText(next, font) > limit) {
                lines.push(current);
                current = grapheme;
            } else {
                current = next;
            }
        });
        if (current || !lines.length) lines.push(current);

        let width = 0;
        lines.forEach(function (line) {
            width = Math.max(width, measurePlainText(line, font));
        });

        return {
            lines: lines,
            lineCount: lines.length,
            width: width,
            height: Math.max(lineHeight, lines.length * lineHeight),
        };
    }

    function measureLines(text, options) {
        const font = getFont(options.font);
        const lineHeight = Math.max(1, Number(options.lineHeight) || 16);
        const maxWidth = Number.isFinite(options.maxWidth) ? Math.max(1, options.maxWidth) : HUGE_WIDTH;
        const whiteSpace = options.whiteSpace || 'normal';
        const normalized = normalizeText(text, whiteSpace);
        if (!normalized) {
            return { text: '', lines: [''], lineCount: 1, width: 0, height: lineHeight, truncated: false };
        }

        if (pretext && typeof pretext.prepareWithSegments === 'function' && typeof pretext.layoutWithLines === 'function') {
            const prepared = getPrepared(normalized, font, whiteSpace, true);
            const result = pretext.layoutWithLines(prepared, maxWidth, lineHeight);
            const lines = Array.isArray(result.lines) && result.lines.length
                ? result.lines.map(function (line) { return String(line.text || ''); })
                : [normalized];
            const width = Array.isArray(result.lines) && result.lines.length
                ? result.lines.reduce(function (max, line) { return Math.max(max, Number(line.width) || 0); }, 0)
                : measurePlainText(normalized, font);
            return {
                text: normalized,
                lines: lines,
                lineCount: Number(result.lineCount) || lines.length,
                width: width,
                height: Number(result.height) || Math.max(lineHeight, lines.length * lineHeight),
                truncated: false,
            };
        }

        const fallback = fallbackLayout(normalized, font, maxWidth, lineHeight, whiteSpace);
        return {
            text: normalized,
            lines: fallback.lines,
            lineCount: fallback.lineCount,
            width: fallback.width,
            height: fallback.height,
            truncated: false,
        };
    }

    function measureDisplay(value, options) {
        const config = Object.assign({
            font: '12px Arial',
            lineHeight: 16,
            maxWidth: HUGE_WIDTH,
            maxLines: Infinity,
            whiteSpace: 'normal',
            suffix: '…',
        }, options || {});
        const normalized = normalizeText(value, config.whiteSpace);
        if (!normalized) {
            return { text: '', lines: [''], lineCount: 1, width: 0, height: config.lineHeight, truncated: false };
        }

        let measured = measureLines(normalized, config);
        if (!Number.isFinite(config.maxLines) || measured.lineCount <= config.maxLines) {
            return measured;
        }

        const graphemes = splitGraphemes(normalized);
        let low = 0;
        let high = graphemes.length;
        let bestText = config.suffix;
        let best = measureLines(bestText, config);

        while (low <= high) {
            const middle = Math.floor((low + high) / 2);
            const candidateText = graphemes.slice(0, middle).join('').trimEnd() + config.suffix;
            const candidate = measureLines(candidateText, config);
            if (candidate.lineCount <= config.maxLines && candidate.width <= config.maxWidth + 0.5) {
                bestText = candidateText;
                best = candidate;
                low = middle + 1;
            } else {
                high = middle - 1;
            }
        }

        best.text = bestText;
        best.truncated = bestText !== normalized;
        return best;
    }

    function fitSingleLine(value, maxWidth, options) {
        return measureDisplay(value, Object.assign({}, options || {}, { maxWidth: maxWidth, maxLines: 1 }));
    }

    function measureLabelGutter(labels, options) {
        const config = Object.assign({
            font: '600 10px Arial',
            lineHeight: 12,
            minWidth: 96,
            maxWidth: 220,
            padding: 28,
            maxLines: 1,
        }, options || {});
        let width = config.minWidth;
        (labels || []).forEach(function (label) {
            const measurement = measureDisplay(label, {
                font: config.font,
                lineHeight: config.lineHeight,
                maxWidth: config.maxWidth,
                maxLines: config.maxLines,
                whiteSpace: config.whiteSpace || 'normal',
            });
            width = Math.max(width, Math.ceil(measurement.width + config.padding));
        });
        return clamp(width, config.minWidth, config.maxWidth);
    }

    function renderSvgTspans(lines, x, y, lineHeight, escapeFn) {
        const escape = typeof escapeFn === 'function'
            ? escapeFn
            : function (value) {
                return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            };
        return (lines || ['']).map(function (line, index) {
            if (index === 0) {
                return '<tspan x="' + x + '" y="' + y + '">' + escape(line) + '</tspan>';
            }
            return '<tspan x="' + x + '" dy="' + lineHeight + '">' + escape(line) + '</tspan>';
        }).join('');
    }

    return {
        ready: !!pretext,
        pretextVersion: pretext && pretext.version ? pretext.version : null,
        splitGraphemes: splitGraphemes,
        measureDisplay: measureDisplay,
        fitSingleLine: fitSingleLine,
        measureLabelGutter: measureLabelGutter,
        measurePlainText: measurePlainText,
        renderSvgTspans: renderSvgTspans,
    };
}));

(function () {
    function _truncateString(value, maxLength) {
        const text = String(value == null ? '' : value);
        if (!maxLength || text.length <= maxLength) return text;
        return text.slice(0, Math.max(0, maxLength - 1)) + '…';
    }

    function _safeJsonStringify(value, maxLength) {
        try {
            return _truncateString(JSON.stringify(value), maxLength || 600);
        } catch (err) {
            return _truncateString(String(value), maxLength || 600);
        }
    }

    function _parseSseBlock(block) {
        const lines = block.split('\n');
        const dataLines = [];
        let eventName = '';
        for (const rawLine of lines) {
            const line = rawLine.replace(/\r$/, '');
            if (!line || line.startsWith(':')) continue;
            const idx = line.indexOf(':');
            const field = idx >= 0 ? line.slice(0, idx) : line;
            const value = idx >= 0 ? line.slice(idx + 1).replace(/^ /, '') : '';
            if (field === 'event') {
                eventName = value;
            } else if (field === 'data') {
                dataLines.push(value);
            }
        }
        const payload = dataLines.join('\n').trim();
        if (!payload) return null;
        try {
            const parsed = JSON.parse(payload);
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                if (eventName && !parsed.type) parsed.type = eventName;
                if (eventName) parsed._sse_event = eventName;
                return parsed;
            }
            return {
                type: eventName || 'DATA',
                data: parsed,
                _sse_event: eventName || '',
            };
        } catch (err) {
            return {
                type: eventName || 'MALFORMED',
                raw: payload,
                error: String(err && err.message || err),
                _sse_event: eventName || '',
            };
        }
    }

    function normalizeTinyFishEvent(event) {
        let normalized;
        if (event && typeof event === 'object' && !Array.isArray(event)) {
            normalized = { ...event };
        } else {
            normalized = { data: event };
        }

        const sseEvent = String(normalized._sse_event || normalized.sse_event || '').trim();
        const payloadType = String(normalized.type || '').trim();
        const resolvedType = (payloadType || sseEvent || 'DATA').trim();

        normalized.type = resolvedType || 'DATA';
        if (sseEvent) normalized._sse_event = sseEvent;
        normalized._tinyfish_type = String(normalized.type || 'DATA').toUpperCase();
        normalized._tinyfish_sse_event = sseEvent ? sseEvent.toUpperCase() : '';
        normalized._tinyfish_type_mismatch = Boolean(
            normalized._tinyfish_sse_event &&
            normalized._tinyfish_type &&
            normalized._tinyfish_sse_event !== normalized._tinyfish_type
        );
        return normalized;
    }

    function _pickFirstPresent(values) {
        for (const value of values) {
            if (value !== null && value !== undefined && value !== '') {
                return value;
            }
        }
        return '';
    }

    function _hasVisiblePayload(event) {
        const normalized = normalizeTinyFishEvent(event);
        const direct = _pickFirstPresent([
            normalized.message,
            normalized.detail,
            normalized.progress,
            normalized.status_message,
            normalized.step,
            normalized.url,
            normalized.streaming_url,
            normalized.raw,
        ]);
        if (direct) return true;
        if (normalized.error) return true;
        if (normalized.data !== undefined) return true;
        if (normalized.result) return true;

        const ignoredKeys = new Set([
            'type',
            '_sse_event',
            '_tinyfish_type',
            '_tinyfish_sse_event',
            '_tinyfish_type_mismatch',
            'run_id',
            'status',
            'timestamp',
            'created_at',
            'started_at',
            'finished_at',
            'submitted_at',
            'site_key',
            'site_name',
        ]);
        return Object.keys(normalized).some((key) => !ignoredKeys.has(key));
    }

    function isIgnorableHeartbeat(event) {
        const normalized = normalizeTinyFishEvent(event);
        const type = normalized._tinyfish_type;
        const sseEvent = normalized._tinyfish_sse_event;
        const heartbeatish = type === 'HEARTBEAT' || sseEvent === 'HEARTBEAT';
        if (!heartbeatish) return false;
        if (normalized._tinyfish_type_mismatch) return false;
        return !_hasVisiblePayload(normalized);
    }

    function countTinyfishItems(result) {
        if (!result || typeof result !== 'object') return 0;
        for (const key of ['items', 'prices', 'plans', 'products', 'results', 'data']) {
            const value = result[key];
            if (Array.isArray(value)) return value.length;
        }
        return 0;
    }

    function formatTinyFishEventDetail(event, options) {
        const normalized = normalizeTinyFishEvent(event);
        const maxLength = options && options.maxLength ? options.maxLength : 600;
        const direct = _pickFirstPresent([
            normalized.message,
            normalized.detail,
            normalized.progress,
            normalized.status_message,
            normalized.step,
            normalized.streaming_url,
            normalized.url,
        ]);
        if (direct) return _truncateString(direct, maxLength);

        if (normalized.result) {
            const count = countTinyfishItems(normalized.result);
            if (count) return `${count} items extracted`;
        }

        if (normalized.error) {
            if (typeof normalized.error === 'string') return _truncateString(normalized.error, maxLength);
            return _safeJsonStringify(normalized.error, maxLength);
        }

        if (normalized.data !== undefined) {
            if (typeof normalized.data === 'string') return _truncateString(normalized.data, maxLength);
            return _safeJsonStringify(normalized.data, maxLength);
        }

        const fallback = {};
        for (const [key, value] of Object.entries(normalized)) {
            if (key.startsWith('_tinyfish_')) continue;
            if (key === 'type' || key === '_sse_event') continue;
            if (value === null || value === undefined || value === '') continue;
            fallback[key] = value;
        }
        if (Object.keys(fallback).length) {
            return _safeJsonStringify(fallback, maxLength);
        }
        return '';
    }

    function getTinyFishEventLabel(event) {
        const normalized = normalizeTinyFishEvent(event);
        if (normalized._tinyfish_type_mismatch) {
            return `${normalized._tinyfish_type} [SSE:${normalized._tinyfish_sse_event}]`;
        }
        return normalized._tinyfish_type || 'EVENT';
    }

    async function consumeJsonSseStream(response, onEvent) {
        if (!response || !response.body || !response.body.getReader) {
            throw new Error('Streaming is not supported in this browser.');
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
            const blocks = buffer.split('\n\n');
            buffer = blocks.pop() || '';
            for (const block of blocks) {
                const event = _parseSseBlock(block);
                if (event) {
                    await onEvent(event);
                }
            }
        }

        buffer += decoder.decode();
        const finalBlock = buffer.replace(/\r\n/g, '\n').trim();
        if (finalBlock) {
            const event = _parseSseBlock(finalBlock);
            if (event) {
                await onEvent(event);
            }
        }
    }

    window.ClawcrossTinyFish = {
        consumeJsonSseStream,
        countTinyfishItems,
        formatTinyFishEventDetail,
        getTinyFishEventLabel,
        isIgnorableHeartbeat,
        normalizeTinyFishEvent,
    };
})();

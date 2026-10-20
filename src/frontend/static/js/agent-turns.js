/* An external agent's conversation as turns, the way Codex shows them: the prompt,
   the steps it took (text between tools, tool calls) folded into one line, and the
   final answer last. A running turn shows its steps as they happen. */
(function (global) {
    const zh = () => (typeof currentLang === 'undefined' ? 'zh-CN' : currentLang) === 'zh-CN';
    const esc = value => escapeHtml(String(value ?? ''));

    function duration(seconds) {
        const total = Math.max(0, Math.round(seconds || 0));
        const minutes = Math.floor(total / 60), rest = total % 60;
        if (zh()) return minutes ? `${minutes}分${rest}秒` : `${rest}秒`;
        return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
    }

    function summary(running, seconds, steps) {
        const head = running ? (zh() ? '处理中' : 'Working') : (zh() ? '已处理' : 'Worked for');
        const time = seconds == null ? '' : ' ' + duration(seconds);
        const count = steps ? ` · ${steps} ${zh() ? '步' : (steps === 1 ? 'step' : 'steps')}` : '';
        return head + time + count;
    }

    function inputText(input) {
        if (input == null || input === '' || (typeof input === 'object' && !Object.keys(input).length)) return '';
        return typeof input === 'string' ? input : JSON.stringify(input, null, 2);
    }

    function toolStatus(status) {
        return status === 'failed' ? 'failed' : status === 'completed' || status == null ? 'completed' : 'running';
    }

    // One step of a finished turn: a history row.
    function stepHtml(row) {
        if (row.role === 'tool') {
            const input = inputText(row.input);
            return `<details class="agent-step agent-step-tool" data-status="${toolStatus(row.status)}">` +
                `<summary><span class="agent-step-mark" aria-hidden="true"></span><span class="agent-step-title">${esc(row.tool_name || (zh() ? '工具' : 'Tool'))}</span></summary>` +
                `<div class="agent-step-body">${input ? `<pre class="agent-step-input">${esc(input)}</pre>` : ''}` +
                `${renderToolPager(row.content || '', {emptyText: zh() ? '（无输出）' : '(no output)'})}</div></details>`;
        }
        if (row.role === 'system') {
            return `<details class="agent-step agent-step-thought"><summary>${zh() ? '思考' : 'Thinking'}</summary>` +
                `<div class="markdown-body">${renderMarkdown(row.content)}</div></details>`;
        }
        return `<div class="agent-step agent-step-text markdown-body">${renderMarkdown(row.content)}</div>`;
    }

    function promptHtml(msg) {
        return '<div class="flex justify-end animate-in fade-in duration-300">' +
            '<div class="p-4 max-w-[85%] shadow-sm bg-blue-600 text-white message-user">' +
            RuntimePresentation.user(msg.content, msg) + '</div></div>';
    }

    function answerHtml(content) {
        return '<div class="flex justify-start animate-in fade-in duration-300">' +
            '<div class="p-4 max-w-[85%] shadow-sm bg-white border text-gray-800 message-agent markdown-body tc-markdown">' +
            renderMarkdown(content) + '</div></div>';
    }

    function errorHtml(content) {
        return '<div class="flex justify-start"><div class="p-4 max-w-[85%] shadow-sm bg-red-50 border border-red-300 text-red-700 message-agent">' +
            '⚠️ ' + esc(content) + '</div></div>';
    }

    // History messages ([{role, content, direction, ts, …}], oldest first) as turns.
    function group(messages) {
        const turns = [];
        let turn = null;
        for (const msg of messages || []) {
            if (!msg || (!msg.content && msg.role !== 'tool')) continue;
            if (msg.direction === 'send' || msg.role === 'user') {
                turn = {prompt: msg, rows: []};
                turns.push(turn);
                continue;
            }
            if (!turn) turns.push(turn = {prompt: null, rows: []});  // a page that starts mid-turn
            turn.rows.push(msg);
        }
        return turns;
    }

    function turnHtml(turn) {
        const errors = turn.rows.filter(row => row.direction === 'error');
        const rows = turn.rows.filter(row => row.direction !== 'error');
        const last = rows[rows.length - 1];
        const answer = last && last.role === 'assistant' ? last : null;
        const steps = answer ? rows.slice(0, -1) : rows;
        const times = [turn.prompt, ...turn.rows].map(row => row && row.ts).filter(Number.isFinite);
        const seconds = times.length > 1 ? times[times.length - 1] - times[0] : null;
        let html = turn.prompt ? promptHtml(turn.prompt) : '';
        if (steps.length) {
            html += `<div class="flex justify-start agent-turn-row"><details class="agent-turn-process"${answer ? '' : ' open'}>` +
                `<summary>${esc(summary(false, seconds, steps.length))}</summary>` +
                `<div class="agent-turn-steps">${steps.map(stepHtml).join('')}</div></details></div>`;
        }
        if (answer) html += answerHtml(answer.content);
        return html + errors.map(row => errorHtml(row.content)).join('');
    }

    function historyHtml(messages) {
        return group(messages).map(turnHtml).join('');
    }

    // The running turn at the end of the conversation, fed with the changed items
    // of GET /v1/agents/{id}/events.
    function live(turn) {
        const root = document.createElement('div');
        root.className = 'agent-turn-live';
        root.innerHTML = '<div class="flex justify-start agent-turn-row"><details class="agent-turn-process" open>' +
            '<summary></summary><div class="agent-turn-steps"></div></details></div>' +
            '<div class="flex justify-start" hidden><div class="p-4 max-w-[85%] shadow-sm bg-white border text-gray-800 message-agent markdown-body tc-markdown"></div></div>';
        const processRow = root.firstElementChild;
        const label = processRow.querySelector('summary');
        const stepsBox = processRow.querySelector('.agent-turn-steps');
        const answerRow = root.lastElementChild;
        const answerBox = answerRow.firstElementChild;
        const items = new Map();  // id -> {item, el}
        let steps = 0;

        function paintLabel() {
            label.textContent = summary(true, Date.now() / 1000 - turn.started_at, steps);
        }

        function paintItem(entry) {
            const {item, el} = entry;
            if (item.kind === 'text') {
                el.innerHTML = renderMarkdown(item.text);
                highlightMarkdownIn(el);
                return;
            }
            el.dataset.status = toolStatus(item.status);
            el.querySelector('.agent-step-title').textContent = item.title || (zh() ? '工具' : 'Tool');
            const input = inputText(item.input);
            const inputEl = el.querySelector('.agent-step-input');
            inputEl.hidden = !input;
            inputEl.textContent = input;
            el.querySelector('.agent-step-output').textContent = item.output || '';
        }

        function update(changed) {
            for (const item of changed || []) {
                let entry = items.get(item.id);
                if (!entry) {
                    const el = document.createElement(item.kind === 'text' ? 'div' : 'details');
                    el.className = item.kind === 'text' ? 'agent-step agent-step-text markdown-body' : 'agent-step agent-step-tool';
                    if (item.kind !== 'text') {
                        el.innerHTML = '<summary><span class="agent-step-mark" aria-hidden="true"></span><span class="agent-step-title"></span></summary>' +
                            '<div class="agent-step-body"><pre class="agent-step-input"></pre><pre class="agent-step-output"></pre></div>';
                    }
                    items.set(item.id, entry = {item, el});
                }
                entry.item = item;
                paintItem(entry);
            }
            // The text after the last tool is the answer so far; everything before it is a step.
            const ordered = [...items.values()];
            const tail = ordered.length && ordered[ordered.length - 1].item.kind === 'text' ? ordered.pop() : null;
            ordered.forEach(entry => stepsBox.appendChild(entry.el));
            steps = ordered.length;
            if (tail) answerBox.appendChild(tail.el);
            answerRow.hidden = !tail;
            processRow.hidden = !steps && !!tail;
            paintLabel();
        }

        const timer = setInterval(() => root.isConnected ? paintLabel() : clearInterval(timer), 1000);
        update([]);
        return {el: root, update, stop: () => clearInterval(timer)};
    }

    global.AgentTurns = {historyHtml, live};
})(window);

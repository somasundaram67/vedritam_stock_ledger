/* Floating chatbot: mode-aware (Stock Ledger vs Library). */
(function () {
    if (window.VAssistant && typeof window.VAssistant.open === 'function') return;
    const CSS = `
    #cbBtn{position:fixed;bottom:20px;right:20px;width:56px;height:56px;border-radius:50%;
        background:#0f3a61;color:#fff;border:none;font-size:26px;cursor:pointer;
        box-shadow:0 4px 14px rgba(0,0,0,.25);z-index:9999}
    #cbBox{position:fixed;bottom:88px;right:20px;width:360px;max-width:calc(100vw - 40px);
        height:500px;max-height:calc(100vh - 120px);background:#fff;border-radius:12px;
        box-shadow:0 8px 30px rgba(0,0,0,.25);display:none;flex-direction:column;z-index:9999;
        font-family:inherit;overflow:hidden}
    #cbBox.open{display:flex}
    #cbHead{background:#0f3a61;color:#fff;padding:12px 14px;font-weight:600;display:flex;
        justify-content:space-between;align-items:center}
    #cbHead span{cursor:pointer;font-size:20px;line-height:1}
    #cbLog{flex:1;overflow-y:auto;padding:12px;background:#f7f8fa;font-size:.9rem}
    .cbMsg{margin-bottom:10px;padding:8px 12px;border-radius:10px;max-width:85%;white-space:pre-wrap;word-wrap:break-word}
    .cbMe{background:#0f3a61;color:#fff;margin-left:auto;border-bottom-right-radius:2px}
    .cbAi{background:#fff;color:#222;border:1px solid #e2e5ea;border-bottom-left-radius:2px}
    .cbSys{background:transparent;color:#888;font-size:.8rem;text-align:center;font-style:italic}
    #cbForm{display:flex;padding:8px;border-top:1px solid #e2e5ea;background:#fff;gap:6px}
    #cbInput{flex:1;border:1px solid #d0d4db;border-radius:6px;padding:8px;font:inherit;outline:none}
    #cbInput:focus{border-color:#0f3a61}
    #cbSend{background:#0f3a61;color:#fff;border:none;border-radius:6px;padding:0 14px;cursor:pointer;font-weight:600}
    #cbSend:disabled{opacity:.5;cursor:wait}
    `;
    const style = document.createElement('style'); style.textContent = CSS; document.head.appendChild(style);

    function currentMode() {
        try { return localStorage.getItem('v_mode') === 'library' ? 'library' : 'ledger'; }
        catch (e) { return 'ledger'; }
    }
    const headTitle = () => currentMode() === 'library' ? '📚 Library Assistant' : '🤖 Ledger Assistant';
    const placeholder = () => currentMode() === 'library'
        ? 'Ask about loans, overdue books, members...'
        : 'Ask about stock, books, schools...';

    const btn = document.createElement('button'); btn.id = 'cbBtn'; btn.innerHTML = '💬'; btn.title = 'Chat assistant';
    const box = document.createElement('div'); box.id = 'cbBox'; box.innerHTML = `
        <div id="cbHead"><div id="cbTitle">${headTitle()}</div><span id="cbClose">×</span></div>
        <div id="cbLog"></div>
        <form id="cbForm"><input id="cbInput" placeholder="${placeholder()}" autocomplete="off"/>
        <button id="cbSend" type="submit">Send</button></form>`;
    document.body.appendChild(btn); document.body.appendChild(box);

    const log = box.querySelector('#cbLog');
    const input = box.querySelector('#cbInput');
    const form = box.querySelector('#cbForm');
    const send = box.querySelector('#cbSend');
    const title = box.querySelector('#cbTitle');

    function refreshHead() {
        title.textContent = headTitle();
        input.placeholder = placeholder();
    }

    function openBox() { refreshHead(); box.classList.add('open'); setTimeout(() => input.focus(), 0); }
    function closeBox() { box.classList.remove('open'); }
    function toggleBox() { box.classList.contains('open') ? closeBox() : openBox(); }

    btn.onclick = toggleBox;
    box.querySelector('#cbClose').onclick = closeBox;
    window.VAssistant = { open: openBox, close: closeBox, toggle: toggleBox };

    function add(cls, text) {
        const d = document.createElement('div'); d.className = 'cbMsg ' + cls; d.textContent = text;
        log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
    }

    const history = [];
    add('cbSys', 'Hi! Ask me anything — I can answer general questions and, when relevant, use your Stock Ledger or Library data (based on the header toggle).');


    fetch('/api/v1/settings/ai', { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('v_token') || '') } })
        .then(r => r.ok ? r.json() : null)
        .then(cfg => {
            if (cfg && !cfg.configured) {
                add('cbSys', cfg.canEdit
                    ? 'No API key saved yet — add one in Settings to enable the assistant.'
                    : 'The assistant is not set up yet. Ask your administrator to add an API key.');
            }
        })
        .catch(() => {});

    form.onsubmit = async (e) => {
        e.preventDefault();
        const q = input.value.trim(); if (!q) return;
        add('cbMe', q);
        input.value = ''; send.disabled = true;
        const thinking = add('cbAi', 'Thinking...');
        try {
            const r = await fetch('/api/v1/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (localStorage.getItem('v_token') || '')
                },
                body: JSON.stringify({ question: q, history, mode: currentMode() })
            });
            let j = {};
            try { j = await r.json(); } catch (_) {}
            if (r.status === 401) throw new Error('Session expired — please log in again.');
            if (!r.ok) throw new Error(j.detail || (j.error && j.error.message) || ('HTTP ' + r.status));
            const answer = j.answer || '(no response)';
            thinking.textContent = answer;
            history.push({ role: 'user', content: q }, { role: 'assistant', content: answer });
            if (history.length > 10) history.splice(0, history.length - 10);
        } catch (err) {
            thinking.textContent = '❌ ' + (err.message === 'Failed to fetch'
                ? 'Cannot reach the server. Is the app running?' : err.message);
        } finally { send.disabled = false; input.focus(); }
    };
})();

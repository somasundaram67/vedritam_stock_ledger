/* messages.js - messaging UI: conversations, thread, attachments,
   read receipts, typing indicator and search. */
(function () {
  var api = function (p, m, b) { return window.App.apiCall(p, m || 'GET', b || null); };
  var me = '';
  try { me = localStorage.getItem('v_username') || ''; } catch (e) {}
  var role = 'user';
  try { role = localStorage.getItem('v_role') || 'user'; } catch (e) {}

  var current = null;      // active conversation id
  var currentConv = null;  // active conversation record (members, type, ...)
  var offset = 0;          // pagination cursor (messages already loaded)
  var PAGE = 40;
  var pollTimer = null, typingTimer = null, lastTypingPing = 0;
  var rawBodies = {};      // message id -> original (encrypted) body envelope

  // Announcements are a public channel and stay readable; every other
  // conversation is end-to-end encrypted in the browser, so the server (and
  // therefore any administrator) only ever stores ciphertext.
  function isPrivate(conv) { return !!conv && conv.type !== 'announcement'; }
  var LOCK = '\uD83D\uDD12';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function toast(m, t) { if (window.App && App.ui) App.ui.showToast(m, t || 'success'); }
  function el(id) { return document.getElementById(id); }

  // ---- avatars -------------------------------------------------------------
  // Direct messages get a per-person avatar. Announcements always use ONE
  // fixed channel avatar (like a WhatsApp channel), never a per-sender one.
  var AV_COLORS = ['#0EA5E9', '#6366F1', '#EC4899', '#F97316', '#14B8A6', '#8B5CF6', '#22C55E', '#E11D48'];
  function avColor(name) {
    var h = 0, s = String(name || '');
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return AV_COLORS[h % AV_COLORS.length];
  }
  function initials(name) {
    var parts = String(name || '?').trim().split(/[\s._-]+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts[0][0] + (parts[1] ? parts[1][0] : (parts[0][1] || ''))).toUpperCase();
  }
  // type: 'dm' | 'group' | 'announcement'
  function avatarHtml(type, name, small) {
    var cls = 'mg-av' + (small ? ' sm' : '');
    if (type === 'announcement') return '<span class="' + cls + ' ann" title="Announcements">&#128226;</span>';
    if (type === 'group') return '<span class="' + cls + ' group" title="' + esc(name) + '">&#128101;</span>';
    return '<span class="' + cls + '" style="background:' + avColor(name) + '">' + esc(initials(name)) + '</span>';
  }
  function convTitle(c) {
    if (c.type === 'announcement') return 'Announcements';
    if (c.type === 'dm') {
      return (c.members || []).filter(function (m) { return m !== me; }).join(', ') || c.title || 'Direct message';
    }
    return c.title || 'Group';
  }
  /* Parses a stored "YYYY-MM-DD HH:MM:SS" (server local time) into a Date. */
  function parseTs(ts) {
    var m = String(ts || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  }
  function dayKey(d) {
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function clockText(ts) {
    var d = parseTs(ts);
    if (!d) return String(ts || '');
    var h = d.getHours(), mm = d.getMinutes();
    var ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12; if (!h) h = 12;
    return h + ':' + (mm < 10 ? '0' + mm : mm) + ' ' + ap;
  }
  function fullWhen(ts) {
    var d = parseTs(ts);
    if (!d) return String(ts || '');
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) +
      ', ' + clockText(ts);
  }
  function shortWhen(ts) {
    var d = parseTs(ts);
    if (!d) return String(ts || '').slice(0, 16);
    var now = new Date();
    if (dayKey(d) === dayKey(now)) return clockText(ts);
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (dayKey(d) === dayKey(y)) return 'Yesterday';
    var sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString(undefined, sameYear
      ? { day: 'numeric', month: 'short' }
      : { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // ---- conversation list ---------------------------------------------------
  function renderList(items) {
    var box = el('mgList');
    if (!items.length) { box.innerHTML = '<div class="mg-empty">No conversations yet.</div>'; return; }
    box.innerHTML = items.map(function (c) {
      var kind = c.type === 'announcement' ? 'announcement' : (c.type === 'group' ? 'group' : 'dm');
      var title = convTitle(c);
      return '<button class="mg-item' + (c.id === current ? ' active' : '') + '" data-id="' + esc(c.id) + '">' +
        avatarHtml(kind, title, false) +
        '<span class="mg-body">' +
          '<span class="mg-top"><b>' + esc(title) + '</b>' +
            '<span class="mg-when">' + esc(shortWhen(c.last_message_at)) + '</span></span>' +
          '<span class="mg-prev"><span>' + esc(c.last_message || 'No messages yet') + '</span>' +
            (c.unread ? '<span class="mg-badge">' + c.unread + '</span>' : '') +
          '</span>' +
        '</span></button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.mg-item'), function (b) {
      b.addEventListener('click', function () { open(b.getAttribute('data-id')); });
    });
  }


  function loadList() {
    return api('/messaging/conversations').then(function (r) { renderList(r.data || []); })
      .catch(function (e) { el('mgList').innerHTML = '<div class="mg-empty">' + esc(e.message) + '</div>'; });
  }

  // ---- thread --------------------------------------------------------------
  function dayLabel(day) {
    var d = parseTs(day + ' 00:00');
    if (!d) return day;
    var today = new Date();
    if (dayKey(d) === dayKey(today)) return 'Today';
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (dayKey(d) === dayKey(y)) return 'Yesterday';
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }

  /* Turns a stored message into something renderable, decrypting when needed. */
  function decryptItem(m, conv) {
    rawBodies[m.id] = m.body;
    if (!isPrivate(conv) || !window.E2EE || !E2EE.isEncrypted(m.body)) {
      return Promise.resolve(m);
    }
    return E2EE.decryptMessage(m.body).then(function (res) {
      var out = {};
      Object.keys(m).forEach(function (k) { out[k] = m[k]; });
      out.body = res.text;
      out.encrypted = true;
      if (res.file) {
        out.attachment_name = res.file.name;
        out.attachment_type = res.file.type;
      }
      return out;
    }).catch(function () {
      var out = {};
      Object.keys(m).forEach(function (k) { out[k] = m[k]; });
      out.body = '';
      out.encrypted = true;
      out.undecryptable = true;
      out.attachment_id = '';
      return out;
    });
  }

  function bubble(m, conv) {
    var mine = m.sender === me;
    var html = '<div class="bubble' + (mine ? ' mine' : '') + '">';
    // Sender names only make sense in groups: a DM already has one avatar in
    // the header, and an announcement channel speaks with a single voice.
    if (!mine && conv && conv.type === 'group') html += '<span class="who">' + esc(m.sender) + '</span>';

    if (m.undecryptable) {
      html += '<span class="mg-locked">' + LOCK + ' Older encrypted message — unavailable.</span>';
    } else if (m.body) {
      html += esc(m.body).replace(/\n/g, '<br>');
    }
    if (m.attachment_id) {
      var url = (window.App.API_BASE_URL || '/api/v1') + '/messaging/attachments/' + encodeURIComponent(m.attachment_id);
      if (/^image\//.test(m.attachment_type || '')) {
        html += '<img class="att-img" alt="' + esc(m.attachment_name) + '" data-att="' + esc(m.attachment_id) + '" data-msg="' + esc(m.id) + '">';
      }
      html += '<a class="att" href="#" data-download="' + esc(m.attachment_id) + '" data-msg="' + esc(m.id) + '" data-name="' + esc(m.attachment_name) + '">' +
        esc(m.attachment_name || 'Attachment') + '</a>';
    }
    var readers = (m.read_by || []).filter(function (u) { return u !== m.sender; });
    var receipt = mine
      ? (readers.length ? ' · Read by ' + esc(readers.join(', ')) : ' · Sent')
      : '';
    html += '<span class="meta" title="' + esc(fullWhen(m.timestamp)) + '">' +
      esc(clockText(m.timestamp)) + receipt + '</span></div>';

    return html;
  }

  /* Downloads an attachment and, when it belongs to an encrypted message,
     decrypts the bytes in the browser before showing or saving them. */
  function fetchBlobUrl(attId, msgId, cb) {
    var token = localStorage.getItem('v_token');
    var raw = rawBodies[msgId];
    fetch((window.App.API_BASE_URL || '/api/v1') + '/messaging/attachments/' + encodeURIComponent(attId),
      { headers: { Authorization: 'Bearer ' + token } })
      .then(function (r) { if (!r.ok) throw new Error('Attachment unavailable'); return r.arrayBuffer(); })
      .then(function (buf) {
        if (window.E2EE && E2EE.isEncrypted(raw)) {
          return E2EE.decryptAttachment(raw, buf).then(function (res) { return res.blob; });
        }
        return new Blob([buf]);
      })
      .then(function (b) { cb(URL.createObjectURL(b)); })
      .catch(function (e) { toast(e.message, 'error'); });
  }

  function hydrateAttachments(scope) {
    Array.prototype.forEach.call(scope.querySelectorAll('img[data-att]'), function (img) {
      fetchBlobUrl(img.getAttribute('data-att'), img.getAttribute('data-msg'), function (u) { img.src = u; });
    });
    Array.prototype.forEach.call(scope.querySelectorAll('a[data-download]'), function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        fetchBlobUrl(a.getAttribute('data-download'), a.getAttribute('data-msg'), function (u) {
          var link = document.createElement('a');
          link.href = u; link.download = a.getAttribute('data-name') || 'attachment';
          document.body.appendChild(link); link.click(); link.remove();
        });
      });
    });
  }

  function renderThread(page, append) {
    var conv = page.conversation || {};
    return Promise.all((page.items || []).map(function (m) { return decryptItem(m, conv); }))
      .then(function (items) { paintThread(page, items, append); });
  }

  function paintThread(page, items, append) {
    var box = el('mgThread');
    var conv = page.conversation || {};
    currentConv = conv;
    var lastDay = '';
    var html = items.map(function (m) {
      var day = String(m.timestamp || '').slice(0, 10);
      var sep = '';
      if (day && day !== lastDay) { lastDay = day; sep = '<div class="mg-daysep">' + esc(dayLabel(day)) + '</div>'; }
      return sep + bubble(m, conv);
    }).join('');

    if (append) {
      var prevHeight = box.scrollHeight;
      box.insertAdjacentHTML('afterbegin', html);
      box.scrollTop = box.scrollHeight - prevHeight;
    } else {
      box.innerHTML = html || '<div class="mg-empty">No messages yet — say hello.</div>';
      box.scrollTop = box.scrollHeight;
    }
    hydrateAttachments(box);
    el('mgOlder').style.display = page.has_more ? '' : 'none';

    var kind = conv.type === 'announcement' ? 'announcement' : (conv.type === 'group' ? 'group' : 'dm');
    var title = convTitle(conv);
    el('mgTitle').textContent = title || 'Conversation';
    var av = el('mgAvatar');
    if (av) {
      av.style.display = '';
      av.className = 'mg-av sm' + (kind === 'announcement' ? ' ann' : kind === 'group' ? ' group' : '');
      av.style.background = kind === 'dm' ? avColor(title) : '';
      av.innerHTML = kind === 'announcement' ? '&#128226;' : kind === 'group' ? '&#128101;' : esc(initials(title));
    }

    el('mgMeta').textContent = (conv.type === 'announcement' ? 'Announcement channel' :
      conv.type === 'group' ? (conv.members || []).length + ' members' : 'Direct message') +
      ' · ' + page.total + ' message' + (page.total === 1 ? '' : 's') +
      '';


    var readOnly = conv.type === 'announcement' && role !== 'super_admin';
    el('mgForm').style.display = readOnly ? 'none' : '';
  }

  function open(id) {
    current = id; offset = 0;
    el('mgThread').innerHTML = '<div class="mg-empty">Loading...</div>';
    api('/messaging/conversations/' + id + '/messages?limit=' + PAGE)
      .then(function (page) {
        offset = (page.items || []).length;
        renderThread(page, false);
        return api('/messaging/conversations/' + id + '/read', 'POST');
      })
      .then(function () {
        if (window.vRefreshMessageBadge) window.vRefreshMessageBadge();
        return loadList();
      })
      .catch(function (e) { el('mgThread').innerHTML = '<div class="mg-empty">' + esc(e.message) + '</div>'; });
  }

  function loadOlder() {
    if (!current) return;
    api('/messaging/conversations/' + current + '/messages?limit=' + PAGE + '&offset=' + offset)
      .then(function (page) { offset += (page.items || []).length; renderThread(page, true); })
      .catch(function (e) { toast(e.message, 'error'); });
  }

  // ---- polling: new messages + typing indicator ---------------------------
  function poll() {
    if (!current) { loadList(); return; }
    api('/messaging/conversations/' + current + '/messages?limit=' + PAGE)
      .then(function (page) {
        var box = el('mgThread');
        var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
        var count = (page.items || []).length;
        if (count !== offset || box.getAttribute('data-total') !== String(page.total)) {
          offset = count;
          box.setAttribute('data-total', String(page.total));
          renderThread(page, false);
          if (!atBottom) box.scrollTop = box.scrollTop;
          api('/messaging/conversations/' + current + '/read', 'POST').then(function () {
            if (window.vRefreshMessageBadge) window.vRefreshMessageBadge();
            return loadList();
          });
        }
        var typing = page.typing || [];
        el('mgTyping').textContent = typing.length
          ? typing.join(', ') + (typing.length === 1 ? ' is typing...' : ' are typing...') : '';
      }).catch(function () {});
  }

  // ---- composing -----------------------------------------------------------
  var pendingFile = null;

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () {
        resolve({ name: file.name, type: file.type, data: String(fr.result).split(',')[1] });
      };
      fr.onerror = function () { reject(new Error('Could not read that file.')); };
      fr.readAsDataURL(file);
    });
  }

  /* Builds the request payload. Messages are stored server-side so that any
     account can read its own conversations from any device or browser. */
  function buildPayload(conv, body, file) {
    return Promise.resolve(file ? readFile(file) : null)
      .then(function (att) { return { body: body, attachment: att }; });
  }

  function send(e) {
    e.preventDefault();
    if (!current) return;
    var input = el('mgInput');
    var body = input.value.trim();
    if (!body && !pendingFile) return;
    var btn = el('mgSend'); btn.disabled = true;
    buildPayload(currentConv, body, pendingFile)
      .then(function (payload) {
        return api('/messaging/conversations/' + current + '/messages', 'POST', payload);
      })
      .then(function () {
        input.value = ''; pendingFile = null;
        el('mgFile').value = ''; el('mgFileName').textContent = '';
        return api('/messaging/conversations/' + current + '/messages?limit=' + PAGE);
      })
      .then(function (page) { offset = (page.items || []).length; renderThread(page, false); return loadList(); })
      .catch(function (err) { toast(err.message, 'error'); })
      .then(function () { btn.disabled = false; });
  }

  // ---- search --------------------------------------------------------------
  /* Search runs in the browser so legacy encrypted messages are searched too. */
  function runSearch(q) {
    if (q.length < 2) { loadList(); return; }
    var box = el('mgList');
    box.innerHTML = '<div class="mg-empty">Searching your messages...</div>';
    var needle = q.toLowerCase();

    api('/messaging/conversations').then(function (r) {
      var convs = r.data || [];
      return Promise.all(convs.map(function (c) {
        return api('/messaging/conversations/' + c.id + '/messages?limit=200')
          .then(function (page) {
            var conv = page.conversation || c;
            return Promise.all((page.items || []).map(function (m) { return decryptItem(m, conv); }))
              .then(function (items) {
                return items.filter(function (m) {
                  var text = (m.body || '') + ' ' + (m.attachment_name || '');
                  return text.toLowerCase().indexOf(needle) !== -1;
                }).map(function (m) {
                  return { conversation_id: c.id, conversation_title: convTitle(conv),
                           sender: m.sender, body: m.body || m.attachment_name || '',
                           timestamp: m.timestamp };
                });
              });
          }).catch(function () { return []; });
      }));
    }).then(function (groups) {
      var hits = [].concat.apply([], groups);
      if (!hits.length) { box.innerHTML = '<div class="mg-empty">No messages match "' + esc(q) + '".</div>'; return; }
      box.innerHTML = hits.map(function (h) {
        var body = esc(h.body);
        var re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
        return '<div class="mg-hit" data-id="' + esc(h.conversation_id) + '">' +
          '<strong>' + esc(h.conversation_title) + '</strong> · ' + esc(h.sender) +
          '<div style="color:var(--v-text-muted);font-size:12.5px">' + body.replace(re, '<em>$1</em>') + '</div>' +
          '<div style="font-size:11px;color:var(--v-text-muted)">' + esc(fullWhen(h.timestamp)) + '</div></div>';
      }).join('');
      Array.prototype.forEach.call(box.querySelectorAll('.mg-hit'), function (d) {
        d.addEventListener('click', function () { open(d.getAttribute('data-id')); });
      });
    }).catch(function (e) { toast(e.message, 'error'); });
  }

  // ---- new conversations (in-page dialogs, no browser prompts) -------------
  function openModal(id) {
    var m = el(id);
    if (!m) return;
    m.classList.add('open');
    var first = m.querySelector('input, textarea');
    if (first) setTimeout(function () { first.focus(); }, 30);
  }
  function closeModal(id) {
    var m = el(id);
    if (!m) return;
    m.classList.remove('open');
    var form = m.querySelector('form');
    if (form) form.reset();
  }
  function bindModals() {
    Array.prototype.forEach.call(document.querySelectorAll('.mg-modal'), function (m) {
      m.addEventListener('click', function (e) { if (e.target === m) closeModal(m.id); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-close]'), function (b) {
      b.addEventListener('click', function () { closeModal(b.getAttribute('data-close')); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      Array.prototype.forEach.call(document.querySelectorAll('.mg-modal.open'), function (m) { closeModal(m.id); });
    });

    el('mgDmForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var who = el('mgDmUser').value.trim();
      if (!who) return;
      api('/messaging/conversations', 'POST', { type: 'dm', members: [who] })
        .then(function (r) { closeModal('mgDmModal'); return loadList().then(function () { open(r.data.id); }); })
        .catch(function (err) { toast(err.message, 'error'); });
    });

    el('mgGroupForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var title = el('mgGroupName').value.trim();
      if (!title) return;
      var members = (el('mgGroupMembers').value || '').split(',')
        .map(function (x) { return x.trim(); }).filter(Boolean);
      api('/messaging/conversations', 'POST', { type: 'group', title: title, members: members })
        .then(function (r) { closeModal('mgGroupModal'); return loadList().then(function () { open(r.data.id); }); })
        .catch(function (err) { toast(err.message, 'error'); });
    });

    el('mgAnnForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var title = el('mgAnnTitle').value.trim();
      var body = el('mgAnnBody').value.trim();
      if (!title || !body) return;
      api('/messaging/announcements', 'POST', { title: title, body: body })
        .then(function (r) {
          closeModal('mgAnnModal');
          toast('Announcement sent to ' + r.recipients + ' account(s).');
          return loadList().then(function () { open(r.data.id); });
        })
        .catch(function (err) { toast(err.message, 'error'); });
    });
  }

  function newDm() { openModal('mgDmModal'); }
  function newGroup() { openModal('mgGroupModal'); }
  function announce() { openModal('mgAnnModal'); }

  // ---- boot ----------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    if (!localStorage.getItem('v_token')) { window.location.replace('index.html'); return; }
    bindModals();
    if (window.E2EE && E2EE.supported()) {
      // Only used to try to read legacy encrypted messages on this device.
      E2EE.ready().catch(function () {});
    }
    el('mgForm').addEventListener('submit', send);
    el('mgOlder').addEventListener('click', loadOlder);
    el('mgNewDm').addEventListener('click', newDm);
    el('mgNewGroup').addEventListener('click', newGroup);
    el('mgAnnounce').addEventListener('click', announce);

    el('mgFile').addEventListener('change', function () {
      pendingFile = this.files && this.files[0] ? this.files[0] : null;
      if (pendingFile && pendingFile.size > 10 * 1024 * 1024) {
        toast('Files must be 10 MB or smaller.', 'error');
        pendingFile = null; this.value = '';
      }
      el('mgFileName').textContent = pendingFile ? 'Attached: ' + pendingFile.name : '';
    });

    var input = el('mgInput');
    input.addEventListener('input', function () {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 150) + 'px';
      var now = Date.now();
      if (current && now - lastTypingPing > 2500) {
        lastTypingPing = now;
        api('/messaging/conversations/' + current + '/typing', 'POST').catch(function () {});
      }
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el('mgForm').requestSubmit(); }
    });

    var search = el('mgSearch');
    search.addEventListener('input', function () {
      clearTimeout(typingTimer);
      var q = search.value.trim();
      typingTimer = setTimeout(function () { runSearch(q); }, 280);
    });

    loadList().then(function () {
      var qp = new URLSearchParams(window.location.search).get('c');
      if (qp) open(qp);
    });
    pollTimer = setInterval(poll, 4000);
    window.addEventListener('beforeunload', function () { clearInterval(pollTimer); });
  });
})();

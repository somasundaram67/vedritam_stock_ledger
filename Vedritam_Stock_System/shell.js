/* shell.js - app shell: theme, header, sidebar and quick actions. */
(function () {
  var THEME_KEY = 'v_theme';
  function currentTheme() { return document.documentElement.getAttribute('data-theme') || 'light'; }
  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
    var i = document.getElementById('vThemeIcon');
    if (i) { i.setAttribute('data-lucide', t === 'dark' ? 'sun' : 'moon'); redrawIcons(); }
  }
  function redrawIcons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function signOut(reason) {
    try {
      if (window.App && App.apiCall) { App.apiCall('/security/logout', 'POST').catch(function () {}); }
    } catch (e) {}
    try {
      ['v_token','v_username','v_role','v_fullname','v_school_id','v_school','v_class'].forEach(function (k) {
        localStorage.removeItem(k);
      });
      localStorage.clear();
    } catch (e) {}
    try { sessionStorage.clear(); } catch (e) {}
    var suffix = reason === 'timeout' ? 'index.html?timeout=1' : 'index.html?logout=1';
    try { window.location.replace(suffix); }
    catch (e) { window.location.href = suffix; }
  }
  window.vSignOut = signOut;

  // Presence heartbeat: marks this user active every 30s.
  function heartbeat() {
    try {
      var u = localStorage.getItem('v_username'); if (!u) return;
      localStorage.setItem('v_last_active_' + u, String(Date.now()));
      if (window.App && App.apiCall) {
        App.apiCall('/presence/ping', 'POST').catch(function(){});
      }
    } catch (e) {}
  }
  setInterval(heartbeat, 30000);

  // --- Client-side idle session timeout -------------------------------------
  // Mirrors the server policy: no interaction for the configured window signs
  // the account out instead of leaving a live session on an unattended screen.
  var idleMinutes = 30;
  try { idleMinutes = Number(localStorage.getItem('v_session_timeout') || 30) || 30; } catch (e) {}
  var lastInteraction = Date.now();
  ['click', 'keydown', 'mousemove', 'touchstart', 'scroll'].forEach(function (evt) {
    document.addEventListener(evt, function () { lastInteraction = Date.now(); }, { passive: true });
  });
  setInterval(function () {
    if (!localStorage.getItem('v_token')) return;
    if (Date.now() - lastInteraction > idleMinutes * 60 * 1000) signOut('timeout');
  }, 30000);
  window.addEventListener('focus', heartbeat);
  document.addEventListener('DOMContentLoaded', heartbeat);

  function openAssistant() {
    function tryOpen() {
      if (window.VAssistant && typeof window.VAssistant.open === 'function') { window.VAssistant.open(); return true; }
      var b = document.getElementById('cbBtn');
      if (b) { b.click(); return true; }
      return false;
    }
    if (tryOpen()) return;
    var script = document.querySelector('script[data-v-chatbot-loader="1"],script[src$="chatbot.js"]');
    if (script) {
      script.addEventListener('load', function () { if (!tryOpen()) toast('Assistant is unavailable on this page.', 'warn'); }, { once: true });
      setTimeout(function () { tryOpen(); }, 350);
      return;
    }
    script = document.createElement('script');
    script.src = 'chatbot.js'; script.defer = true;
    script.setAttribute('data-v-chatbot-loader', '1');
    script.onload = function () { if (!tryOpen()) toast('Assistant is unavailable on this page.', 'warn'); };
    script.onerror = function () { toast('Assistant could not be loaded.', 'warn'); };
    document.body.appendChild(script);
  }

  var NAV_LEDGER = [
    { label: 'Dashboard', icon: 'layout-dashboard', href: 'dashboard.html', page: 'dashboard' },
    { label: 'Schools', icon: 'school', page: 'schools', children: [
      { label: 'All Schools', href: 'schools.html', page: 'schools' },
      { label: 'Add School', href: 'schools.html?add=1', page: 'schools-add' },
      { label: 'School Analytics', href: 'reports.html#schools', page: 'schools-analytics' }
    ]},
    { label: 'Distribution', icon: 'send', href: 'distribution.html', page: 'distribution' },
    { label: 'Transfers', icon: 'repeat', href: 'transfers.html', page: 'transfers' },
    { label: 'Messages', icon: 'message-square', href: 'messages.html', page: 'messages' },
    { label: 'Reports', icon: 'bar-chart-3', href: 'reports.html', page: 'reports' },
    { label: 'Users', icon: 'users', href: 'users.html', page: 'users', admin: true, staff: true },
    { label: 'To-do', icon: 'list-checks', href: 'todo.html', page: 'todo' },
    { label: 'Activity Log', icon: 'history', href: 'activity.html', page: 'activity', admin: true },
    { label: 'Security', icon: 'shield-check', href: 'security.html', page: 'security', admin: true },
    { label: 'Settings', icon: 'settings', href: 'settings.html', page: 'settings', admin: true }
  ];
  // RBAC: super_admin (everything), staff (assigned schools), user (own records).
  function currentRole() {
    try { return String(localStorage.getItem('v_role') || 'user'); } catch (e) { return 'user'; }
  }
  function isSuperAdmin() { return currentRole() === 'super_admin'; }
  function isStaff() { return currentRole() === 'staff'; }
  function isAdmin() { return isSuperAdmin(); }

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function icon(name, cls) { return '<i data-lucide="' + name + '"' + (cls ? ' class="' + cls + '"' : '') + '></i>'; }

  function navItemHTML(item, active) {
    var isActive = item.page === active;
    return '<li><a href="' + item.href + '" class="' + (isActive ? 'active' : '') + '">' +
      (item.icon ? icon(item.icon) : '<span style="width:6px"></span>') +
      '<span class="v-txt">' + esc(item.label) + '</span></a></li>';
  }

  function buildSidebar(active) {
    var NAV = NAV_LEDGER;
    var html = '<div class="v-side-label">Workspace</div><ul class="v-nav">';
    var admin = isSuperAdmin();
    var staff = isStaff();
    NAV.forEach(function (item) {
      if (item.admin && !admin && !(item.staff && staff)) return;
      if (item.children) {
        var open = item.children.some(function (c) { return c.page === active; }) || item.page === active;
        html += '<li class="v-group' + (open ? ' open' : '') + '">' +
          '<button class="v-group-btn" type="button">' + icon(item.icon) +
          '<span class="v-txt">' + esc(item.label) + '</span>' +
          '<i data-lucide="chevron-right" class="v-chev"></i></button>' +
          '<ul class="v-sub">' + item.children.map(function (c) { return navItemHTML(c, active); }).join('') + '</ul></li>';
      } else {
        html += navItemHTML(item, active);
      }
    });
    return html + '</ul>';
  }

  function build() {
    var body = document.body;
    var active = body.getAttribute('data-page') || '';
    var username = 'User';
    try { username = localStorage.getItem('v_username') || 'User'; } catch (e) {}
    var initials = username.slice(0, 2).toUpperCase();
    var photo = '';
    try { photo = localStorage.getItem('v_avatar_' + username) || ''; } catch (e) {}
    var avatarHTML = photo
      ? '<span class="v-avatar"><img src="' + esc(photo) + '" alt="Profile photo"></span>'
      : '<span class="v-avatar">' + esc(initials) + '</span>';

    var old = document.querySelector('header.app-header');
    if (old) old.remove();

    var brandTitle = 'Vedritam School Stock Ledger';

    var header = document.createElement('header');
    header.className = 'v-header';
    header.innerHTML =
      '<button class="v-burger" id="vBurger" aria-label="Toggle navigation">' + icon('menu') + '</button>' +
      '<div class="v-brand"><img src="logo.png" alt="Vedritam" onerror="this.style.display=\'none\'">' +
      '<b id="vBrandTitle">' + esc(brandTitle) + '</b></div>' +
      '<div class="v-search">' + icon('search') +
      '<input id="vGlobalSearch" type="search" placeholder="Search..." aria-label="Search"></div>' +
      '<div class="v-head-actions">' +
        '<button class="v-icon-btn" id="vBell" aria-label="Notifications">' + icon('bell') + '<span class="v-dot"></span></button>' +
        '<button class="v-icon-btn" id="vTheme" aria-label="Toggle theme"><i data-lucide="moon" id="vThemeIcon"></i></button>' +
        '<span id="vPresence" title="You are online" style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:#16A34A;padding:4px 10px;border-radius:999px;background:rgba(22,163,74,.12)"><span style="width:8px;height:8px;border-radius:50%;background:#16A34A;box-shadow:0 0 0 3px rgba(22,163,74,.2)"></span>Online</span>' +
        '<button class="v-user" id="vUserBtn">' + avatarHTML +
        '<span id="navUsername">' + esc(username) + '</span>' + icon('chevron-down') + '</button>' +
      '</div>';

    var menu = document.createElement('div');
    menu.className = 'v-menu'; menu.id = 'vUserMenu';
    menu.innerHTML = '<a href="settings.html">' + icon('settings') + 'Settings</a>' +
      '<a href="reports.html">' + icon('bar-chart-3') + 'Reports</a><hr>' +
      '<button type="button" id="vLogout">' + icon('log-out') + 'Log out</button>';

    var notifications = document.createElement('div');
    notifications.className = 'v-notifications'; notifications.id = 'vNotifications';
    notifications.innerHTML = '<div class="v-notifications-head"><strong>Notifications</strong>' +
      '<button type="button" id="vNotesReadAll" style="margin-left:auto;background:none;border:0;color:var(--v-primary);font:inherit;font-size:12px;font-weight:650;cursor:pointer">Mark all read</button>' +
      '<button type="button" id="vNotificationsClose" aria-label="Close notifications">' + icon('x') + '</button></div>' +
      '<div class="v-notes-tabs" id="vNotesTabs">' +
        ['all','message','report','announcement','alert'].map(function (t) {
          return '<button type="button" class="v-notes-tab' + (t === 'all' ? ' active' : '') + '" data-nt="' + t + '">' +
            (t === 'all' ? 'All' : t.charAt(0).toUpperCase() + t.slice(1) + 's') + '</button>';
        }).join('') +
      '</div>' +
      '<div class="v-notifications-body" id="vNotificationsBody"><div class="v-note">Loading...</div></div>';

    var side = document.createElement('aside');
    side.className = 'v-sidebar'; side.innerHTML = buildSidebar(active);

    var scrim = document.createElement('div'); scrim.className = 'v-scrim';

    var fab = document.createElement('div');
    fab.className = 'v-fab-wrap'; fab.id = 'vFab';
    var fabItems = '<a href="ledger.html">' + icon('plus-circle') + 'Add Stock</a>' +
        '<a href="distribution.html">' + icon('send') + 'Distribute</a>' +
        '<a href="transfers.html">' + icon('repeat') + 'Transfer</a>' +
        '<a href="todo.html">' + icon('list-checks') + 'To-do list</a>';

    fab.innerHTML =
      '<div class="v-fab-items">' + fabItems +
        '<button type="button" id="vFabChat">' + icon('message-circle') + 'Ask Assistant</button>' +
      '</div>' +
      '<button class="v-fab" id="vFabBtn" aria-label="Quick actions">' + icon('plus') + '</button>';

    body.insertBefore(header, body.firstChild);
    body.insertBefore(menu, header.nextSibling);
    body.insertBefore(notifications, menu.nextSibling);
    body.insertBefore(side, notifications.nextSibling);
    body.insertBefore(scrim, side.nextSibling);
    body.appendChild(fab);

    var main = document.querySelector('main.main-content') || document.querySelector('.main-content');
    if (main) main.classList.add('v-main');

    wire(scrim);
    redrawIcons();
  }

  function wire(scrim) {
    var mq = window.matchMedia('(max-width: 900px)');
    document.getElementById('vBurger').addEventListener('click', function () {
      document.body.classList.toggle(mq.matches ? 'v-mobile-open' : 'v-collapsed');
    });
    scrim.addEventListener('click', function () { document.body.classList.remove('v-mobile-open'); });

    document.getElementById('vTheme').addEventListener('click', function () {
      setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      document.dispatchEvent(new CustomEvent('v-theme-change', { detail: currentTheme() }));
    });

    var userBtn = document.getElementById('vUserBtn');
    var menu = document.getElementById('vUserMenu');
    var notifications = document.getElementById('vNotifications');
    userBtn.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (notifications) notifications.classList.remove('open');
      menu.classList.toggle('open');
    });
    menu.addEventListener('click', function (e) { e.stopPropagation(); });
    document.addEventListener('click', function () {
      menu.classList.remove('open');
      if (notifications) notifications.classList.remove('open');
    });

    var logoutBtn = document.getElementById('vLogout');
    if (logoutBtn) logoutBtn.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation(); window.vSignOut();
    });
    // Any click inside the logout row signs out.
    menu.addEventListener('click', function (e) {
      var t = e.target.closest && e.target.closest('#vLogout');
      if (t) { e.preventDefault(); e.stopPropagation(); window.vSignOut(); }
    });


    Array.prototype.forEach.call(document.querySelectorAll('.logout-btn'), function (b) {
      b.onclick = null;
      b.addEventListener('click', function (ev) { ev.preventDefault(); window.vSignOut(); });
    });

    var bell = document.getElementById('vBell');
    bell.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      menu.classList.remove('open');
      if (notifications) notifications.classList.toggle('open');
    });
    if (notifications) {
      notifications.addEventListener('click', function (e) { e.stopPropagation(); });
      var closeNotes = document.getElementById('vNotificationsClose');
      if (closeNotes) closeNotes.addEventListener('click', function (e) {
        e.preventDefault(); e.stopPropagation(); notifications.classList.remove('open');
      });
    }

    var noteFilter = 'all';
    refreshNotifications();
    setInterval(refreshNotifications, 45000);

    Array.prototype.forEach.call(document.querySelectorAll('.v-notes-tab'), function (t) {
      t.addEventListener('click', function (e) {
        e.stopPropagation();
        noteFilter = t.getAttribute('data-nt');
        Array.prototype.forEach.call(document.querySelectorAll('.v-notes-tab'), function (o) {
          o.classList.toggle('active', o === t);
        });
        refreshNotifications();
      });
    });

    var readAll = document.getElementById('vNotesReadAll');
    if (readAll) readAll.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (!(window.App && App.apiCall)) return;
      App.apiCall('/notifications/read-all', 'POST').then(refreshNotifications).catch(function () {});
    });

    // Notification Center: messages, reports, announcements and alerts.
    function refreshNotifications() {
      var body = document.getElementById('vNotificationsBody');
      var dot = document.querySelector('#vBell .v-dot');
      if (!body) return;
      if (!(window.App && App.apiCall)) { body.innerHTML = '<div class="v-note">You are all caught up.</div>'; return; }
      var path = '/notifications?limit=25' + (noteFilter === 'all' ? '' : '&type=' + noteFilter);
      App.apiCall(path).then(function (r) {
        var items = r.items || [];
        if (dot) dot.style.display = r.unread > 0 ? '' : 'none';
        if (!items.length) {
          body.innerHTML = '<div class="v-note"><strong>No notifications</strong><span>You are all caught up.</span></div>';
          return;
        }
        body.innerHTML = items.map(function (n) {
          var cls = n.type === 'alert' ? ' warn' : '';
          return '<div class="v-note' + cls + (n.read === '1' ? ' read' : '') + '" data-nid="' + esc(n.id) + '"' +
            (n.link ? ' data-link="' + esc(n.link) + '"' : '') + '>' +
            '<strong>' + esc(n.title || 'Notification') + '</strong>' +
            '<span>' + esc(n.body || '') + '</span>' +
            '<span style="opacity:.7;font-size:11px">' + esc(n.timestamp) + ' · ' + esc(n.type) + '</span></div>';
        }).join('');
        Array.prototype.forEach.call(body.querySelectorAll('[data-nid]'), function (d) {
          d.addEventListener('click', function () {
            App.apiCall('/notifications/' + d.getAttribute('data-nid') + '/read', 'POST')
              .then(function () {
                var link = d.getAttribute('data-link');
                if (link) window.location.href = link; else refreshNotifications();
              }).catch(function () {});
          });
        });
      }).catch(function () { body.innerHTML = '<div class="v-note">Could not load notifications.</div>'; });
    }
    window.vRefreshNotifications = refreshNotifications;

    // Unread message badge on the sidebar entry.
    function refreshMessageBadge() {
      if (!(window.App && App.apiCall)) return;
      App.apiCall('/messaging/unread').then(function (r) {
        var link = document.querySelector('.v-nav a[href="messages.html"]');
        if (!link) return;
        var badge = link.querySelector('.v-nav-badge');
        if (!r.total) { if (badge) badge.remove(); return; }
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'v-nav-badge';
          link.appendChild(badge);
        }
        badge.textContent = r.total > 99 ? '99+' : String(r.total);
      }).catch(function () {});
    }
    refreshMessageBadge();
    setInterval(refreshMessageBadge, 30000);

    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('[data-soon="1"]');
      if (a) { e.preventDefault(); toast('This module is not built yet — coming in a later release.', 'warn'); }
    });

    Array.prototype.forEach.call(document.querySelectorAll('.v-group-btn'), function (b) {
      b.addEventListener('click', function () {
        if (document.body.classList.contains('v-collapsed')) {
          document.body.classList.remove('v-collapsed');
          b.parentElement.classList.add('open');
          return;
        }
        b.parentElement.classList.toggle('open');
      });
    });

    var fab = document.getElementById('vFab');
    document.getElementById('vFabBtn').addEventListener('click', function (e) {
      e.stopPropagation(); fab.classList.toggle('open');
    });
    document.addEventListener('click', function () { fab.classList.remove('open'); });
    document.getElementById('vFabChat').addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation(); openAssistant();
    });

    var search = document.getElementById('vGlobalSearch');
    if (search) {
      try {
        var qp = new URLSearchParams(window.location.search).get('q');
        if (qp) search.value = qp;
      } catch (e) {}
      search.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        var q = search.value.trim();
        if (!q) return;
        var onSchools = document.body.getAttribute('data-page') === 'schools';
        if (onSchools && window.App && App.schools && typeof App.schools.applySearch === 'function') {
          App.schools.applySearch(q);
        } else {
          window.location.href = 'schools.html?q=' + encodeURIComponent(q);
        }
      });
    }
  }

  function toast(msg, type) {
    if (window.App && App.ui && App.ui.showToast) { App.ui.showToast(msg, type === 'warn' ? 'error' : 'success'); return; }
    var t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:200;' +
      'background:#1E293B;color:#fff;padding:12px 18px;border-radius:12px;font:500 14px Inter,sans-serif;' +
      'box-shadow:0 12px 32px -12px rgba(0,0,0,.5)';
    document.body.appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(function () { t.remove(); }, 320); }, 3200);
  }

  window.VShell = { logout: window.vSignOut, openAssistant: openAssistant, setTheme: setTheme,
                    theme: currentTheme,
                    toast: toast, refreshIcons: redrawIcons };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();

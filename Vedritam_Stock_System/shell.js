/* shell.js - app shell: theme, header, sidebar and quick actions. */
(function () {
  var THEME_KEY = 'v_theme';
  var MODE_KEY = 'v_mode';   // 'ledger' | 'library'
  function currentTheme() { return document.documentElement.getAttribute('data-theme') || 'light'; }
  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
    var i = document.getElementById('vThemeIcon');
    if (i) { i.setAttribute('data-lucide', t === 'dark' ? 'sun' : 'moon'); redrawIcons(); }
  }
  function currentMode() {
    try { return localStorage.getItem(MODE_KEY) === 'library' ? 'library' : 'ledger'; }
    catch (e) { return 'ledger'; }
  }
  function setMode(m) {
    try { localStorage.setItem(MODE_KEY, m === 'library' ? 'library' : 'ledger'); } catch (e) {}
  }
  function redrawIcons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function signOut() {
    try {
      ['v_token','v_username','v_role','v_fullname','v_school_id','v_school','v_class'].forEach(function (k) {
        localStorage.removeItem(k);
      });
      localStorage.clear();
    } catch (e) {}
    try { sessionStorage.clear(); } catch (e) {}
    try { window.location.replace('index.html?logout=1'); }
    catch (e) { window.location.href = 'index.html?logout=1'; }
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
    { label: 'Reports', icon: 'bar-chart-3', href: 'reports.html', page: 'reports' },
    { label: 'Users', icon: 'users', href: 'users.html', page: 'users', admin: true },
    { label: 'Activity Log', icon: 'history', href: 'activity.html', page: 'activity', admin: true },
    { label: 'Settings', icon: 'settings', href: 'settings.html', page: 'settings', admin: true }
  ];
  var NAV_LIBRARY = [
    { label: 'Dashboard', icon: 'layout-dashboard', href: 'dashboard.html', page: 'dashboard' },
    { label: 'Books', icon: 'book-open', href: 'library.html', page: 'library' },
    { label: 'Members', icon: 'user-square-2', href: 'library-members.html', page: 'library-members' },
    { label: 'Loans', icon: 'book-marked', href: 'library-loans.html', page: 'library-loans' },
    { label: 'Users', icon: 'users', href: 'users.html', page: 'users', admin: true },
    { label: 'Activity Log', icon: 'history', href: 'activity.html', page: 'activity', admin: true },
    { label: 'Settings', icon: 'settings', href: 'settings.html', page: 'settings', admin: true }
  ];

  function isAdmin() { try { return localStorage.getItem('v_role') === 'admin'; } catch (e) { return false; } }

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
    var mode = currentMode();
    var NAV = mode === 'library' ? NAV_LIBRARY : NAV_LEDGER;
    var html = '<div class="v-side-label">' + (mode === 'library' ? 'Library' : 'Workspace') + '</div><ul class="v-nav">';
    var admin = isAdmin();
    NAV.forEach(function (item) {
      if (item.admin && !admin) return;
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

  function modeToggleHTML() {
    var m = currentMode();
    var css = 'display:inline-flex;background:rgba(148,163,184,.15);border-radius:999px;padding:3px;gap:2px;';
    var btn = 'border:0;background:transparent;font:600 12px Inter,sans-serif;padding:6px 12px;border-radius:999px;color:inherit;cursor:pointer;';
    var on = 'background:var(--v-primary,#2563EB);color:#fff;';
    return '<div class="v-mode" style="' + css + '">' +
      '<button type="button" id="vModeLedger" style="' + btn + (m === 'ledger' ? on : '') + '">Stock</button>' +
      '<button type="button" id="vModeLibrary" style="' + btn + (m === 'library' ? on : '') + '">Library</button>' +
      '</div>';
  }

  function build() {
    var body = document.body;
    var active = body.getAttribute('data-page') || '';
    body.setAttribute('data-mode', currentMode());
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

    var brandTitle = currentMode() === 'library' ? 'Vedritam School Library' : 'Vedritam School Stock Ledger';

    var header = document.createElement('header');
    header.className = 'v-header';
    header.innerHTML =
      '<button class="v-burger" id="vBurger" aria-label="Toggle navigation">' + icon('menu') + '</button>' +
      '<div class="v-brand"><img src="logo.png" alt="Vedritam" onerror="this.style.display=\'none\'">' +
      '<b id="vBrandTitle">' + esc(brandTitle) + '</b></div>' +
      '<div class="v-search">' + icon('search') +
      '<input id="vGlobalSearch" type="search" placeholder="Search..." aria-label="Search"></div>' +
      '<div class="v-head-actions">' +
        modeToggleHTML() +
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
    notifications.innerHTML = '<div class="v-notifications-head"><strong>Notifications</strong><button type="button" id="vNotificationsClose" aria-label="Close notifications">' + icon('x') + '</button></div>' +
      '<div class="v-notifications-body" id="vNotificationsBody"><div class="v-note">Loading...</div></div>';

    var side = document.createElement('aside');
    side.className = 'v-sidebar'; side.innerHTML = buildSidebar(active);

    var scrim = document.createElement('div'); scrim.className = 'v-scrim';

    var fab = document.createElement('div');
    fab.className = 'v-fab-wrap'; fab.id = 'vFab';
    var fabItems = currentMode() === 'library'
      ? '<a href="library.html">' + icon('plus-circle') + 'Add Book</a>' +
        '<a href="library-members.html">' + icon('user-plus') + 'Add Member</a>' +
        '<a href="library-loans.html">' + icon('book-marked') + 'Issue Book</a>' +
        '<a href="todo.html">' + icon('list-checks') + 'To-do list</a>'
      : '<a href="ledger.html">' + icon('plus-circle') + 'Add Stock</a>' +
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

    function switchMode(m) {
      if (currentMode() === m) return;
      setMode(m);
      // Navigate so the nav, brand and quick actions re-render for the new mode.
      var ledgerPages = ['distribution', 'transfers'];
      var libraryPages = ['library', 'library-members', 'library-loans'];
      var page = document.body.getAttribute('data-page') || '';
      if (m === 'library' && ledgerPages.indexOf(page) !== -1) window.location.href = 'library.html';
      else if (m === 'ledger' && libraryPages.indexOf(page) !== -1) window.location.href = 'dashboard.html';
      else window.location.reload();
    }
    var lb = document.getElementById('vModeLedger');
    var lib = document.getElementById('vModeLibrary');
    if (lb) lb.addEventListener('click', function () { switchMode('ledger'); });
    if (lib) lib.addEventListener('click', function () { switchMode('library'); });

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

    refreshNotifications();

    function refreshNotifications() {
      var body = document.getElementById('vNotificationsBody');
      if (!body) return;
      body.innerHTML = '<div class="v-note">Loading...</div>';
      if (!(window.App && App.apiCall)) { body.innerHTML = '<div class="v-note">You are all caught up.</div>'; return; }
      var mode = currentMode();
      if (mode === 'library') {
        App.apiCall('/library/reminders').then(function (data) {
          var over = (data && data.counts && data.counts.overdue) || 0;
          var soon = (data && data.counts && data.counts.due_soon) || 0;
          var html = '';
          if (over > 0) html += '<div class="v-note warn"><strong>' + over + ' overdue book' + (over === 1 ? '' : 's') + '</strong><span>Members have books past their due date.</span></div>';
          if (soon > 0) html += '<div class="v-note"><strong>' + soon + ' due within 3 days</strong><span>Send reminders to borrowers.</span></div>';
          (data.overdue || []).slice(0, 4).forEach(function (l) {
            html += '<div class="v-note warn"><strong>' + esc(l.book_title || 'Book') + '</strong><span>' + esc(l.member_name || '') + ' (UID ' + esc(l.member_uid || '') + ') · due ' + esc(l.due_at || '') + '</span></div>';
          });
          body.innerHTML = html || '<div class="v-note"><strong>No pending returns</strong><span>Every book is on time.</span></div>';
          if (bell) {
            var dot = bell.querySelector('.v-dot');
            if (dot) dot.style.display = (over + soon) > 0 ? '' : 'none';
          }
        }).catch(function () { body.innerHTML = '<div class="v-note">Could not load reminders.</div>'; });
      } else {
        App.apiCall('/dashboard').then(function (data) {
          var low = data && data.kpis && data.kpis.lowStock ? Number(data.kpis.lowStock.value || 0) : 0;
          var latest = data && Array.isArray(data.activity) ? data.activity.slice(0, 3) : [];
          var html = '';
          if (low > 0) html += '<div class="v-note warn"><strong>Low stock alert</strong><span>' + low + ' row' + (low === 1 ? ' is' : 's are') + ' below the low-stock threshold.</span></div>';
          latest.forEach(function (a) {
            html += '<div class="v-note"><strong>' + esc(a.text || 'Activity update') + '</strong><span>' + esc(a.meta || '') + '</span></div>';
          });
          body.innerHTML = html || '<div class="v-note"><strong>No new notifications</strong><span>You are all caught up.</span></div>';
        }).catch(function () { body.innerHTML = '<div class="v-note">Could not load notifications.</div>'; });
      }
    }

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
                    theme: currentTheme, mode: currentMode, setMode: setMode,
                    toast: toast, refreshIcons: redrawIcons };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();

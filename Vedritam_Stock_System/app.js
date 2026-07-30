/* app.js - Vedritam School Stock Ledger Management System */

const App = (function() {
    const USE_MOCK_API = false;

    /* API base URL, overridable with localStorage.v_api_base. */
    const API_BASE_URL = (function () {
        const override = (localStorage.getItem('v_api_base') || '').replace(/\/+$/, '');
        if (override) return override + '/api/v1';
        if (location.protocol === 'http:' || location.protocol === 'https:') return '/api/v1';
        return 'http://127.0.0.1:8000/api/v1';   // opened via file:// — talk to the local server
    })();

    const state = {
        token: localStorage.getItem('v_token'), username: localStorage.getItem('v_username'), role: localStorage.getItem('v_role'),
        selectedSchool: JSON.parse(localStorage.getItem('v_school') || 'null'),
        selectedClass: JSON.parse(localStorage.getItem('v_class') || 'null'),
        selectedRowId: null,
        ledgerData: [], dirtyRecords: new Map(), deletedIds: new Set()
    };

    const ui = {
        showToast(message, type = 'success') {
            let container = document.getElementById('toast-container');
            if (!container) {
                container = document.createElement('div');
                container.id = 'toast-container';
                document.body.appendChild(container);
            }
            const toast = document.createElement('div'); toast.className = `toast ${type}`; toast.textContent = message;
            container.appendChild(toast);
            setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3500);
        },
        openModal(id) { document.getElementById(id).classList.add('active'); },
        closeModal(id) { document.getElementById(id).classList.remove('active'); const f = document.querySelector(`#${id} form`); if(f) f.reset(); },
        escapeHTML(str) { return String(str ?? '').replace(/[&<>'"]/g, tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag])); }
    };

    async function apiCall(endpoint, method = 'GET', body = null) {
        const headers = { 'Content-Type': 'application/json' };
        if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
        let res;
        try {
            res = await fetch(`${API_BASE_URL}${endpoint}`, { method, headers, body: body ? JSON.stringify(body) : null });
        } catch (networkError) {
            // Network-level failure: server down or unreachable.
            throw new Error(
                'Cannot reach the Vedritam server. Start it with "python start.py" and open ' +
                'http://127.0.0.1:8000 in your browser (do not open index.html directly from the folder).'
            );
        }
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) {
            const p = window.location.pathname;
            const onIndex = p.endsWith('index.html') || p === '/' || p === '';
            const detail = data.detail;
            const message = typeof detail === 'string' ? detail : 'Session expired.';
            if (onIndex && endpoint === '/auth/login') throw new Error(message);
            if (onIndex) { localStorage.clear(); state.token = null; }
            else logout();
            throw new Error("Session expired.");
        }
        if (!res.ok) {
            const detail = data.detail;
            throw new Error(typeof detail === 'string' ? detail
                : Array.isArray(detail) ? (detail[0]?.msg || `Error ${res.status}`)
                : `Error ${res.status}`);
        }
        return data;
    }

    async function checkAuth() {
        const path = window.location.pathname;
        const isIndex = path.endsWith('index.html') || path === '/' || path === '';
        // Explicit sign-out / "switch account" link: /?logout=1
        if (isIndex && /[?&]logout=1/.test(window.location.search)) {
            localStorage.clear();
            state.token = null;
            history.replaceState(null, '', window.location.pathname);
        }
        if (!state.token && !isIndex) {
            window.location.href = 'index.html';
            return false;
        }
        // The root link always opens the login page first.
        applyRoleVisibility();
        const userLbl = document.getElementById('navUsername'); if (userLbl) userLbl.textContent = state.username || 'User';
        return true;
    }

    function applyRoleVisibility() {
        const isAdmin = state.role === 'admin';
        document.querySelectorAll('.admin-only').forEach(el => { el.style.display = isAdmin ? '' : 'none'; });
        document.querySelectorAll('.user-only').forEach(el => { el.style.display = isAdmin ? 'none' : ''; });
    }

    async function login(e) {
        e.preventDefault();
        const btn = document.getElementById('loginBtn'); btn.textContent = 'Authenticating...'; btn.disabled = true;
        try {
            const res = await apiCall('/auth/login', 'POST', { username: document.getElementById('username').value.trim(), password: document.getElementById('password').value });
            localStorage.setItem('v_token', res.access_token);
            localStorage.setItem('v_username', res.username);
            localStorage.setItem('v_role', res.role);
            localStorage.setItem('v_fullname', res.fullName || res.username);
            localStorage.setItem('v_school_id', res.school_id || '');
            window.location.href = 'dashboard.html';
        } catch (error) { ui.showToast(error.message, 'error'); btn.textContent = 'Authenticate'; btn.disabled = false; }
    }

    async function signup(e) {
        e.preventDefault();
        const pwd = document.getElementById('suPassword').value;
        const pwd2 = document.getElementById('suPassword2').value;
        if (pwd !== pwd2) return ui.showToast('The two passwords do not match.', 'error');
        if (pwd.length < 6) return ui.showToast('Password must be at least 6 characters.', 'error');

        const btn = document.getElementById('signupBtn'); btn.textContent = 'Submitting...'; btn.disabled = true;
        try {
            const res = await apiCall('/auth/signup', 'POST', {
                username: document.getElementById('suUsername').value.trim(),
                password: pwd,
                fullName: document.getElementById('suFullName').value.trim(),
                email: document.getElementById('suEmail').value.trim(),
                schoolName: document.getElementById('suSchool').value.trim()
            });
            ui.showToast(res.message || 'Request submitted.', 'success');
            document.getElementById('signupForm').reset();
            auth.showTab('signin');
        } catch (error) { ui.showToast(error.message, 'error'); }
        finally { btn.textContent = 'Submit Request'; btn.disabled = false; }
    }

    const auth = {
        showTab(name) {
            const signin = name === 'signin';
            const pi = document.getElementById('paneSignin');
            const pu = document.getElementById('paneSignup');
            if (!pi || !pu) return;
            pi.style.display = signin ? '' : 'none';
            pu.style.display = signin ? 'none' : '';
            document.querySelectorAll('.auth-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
        },
        init() {
            const f = document.getElementById('loginForm'); if (f) f.addEventListener('submit', login);
            const sf = document.getElementById('signupForm'); if (sf) sf.addEventListener('submit', signup);
            document.querySelectorAll('.auth-tab').forEach(t => t.addEventListener('click', () => this.showTab(t.dataset.tab)));
            document.querySelectorAll('[data-goto]').forEach(a => a.addEventListener('click', (e) => { e.preventDefault(); this.showTab(a.dataset.goto); }));
        },
        async changePassword(currentPassword, newPassword) {
            return apiCall('/auth/change-password', 'POST', { currentPassword, newPassword });
        }
    };

    function clearSessionAndRedirect() {
        try { localStorage.clear(); } catch (e) {}
        try { sessionStorage.clear(); } catch (e) {}
        window.location.replace('index.html?logout=1');
    }

    function logout() {
        if (window.vSignOut && window.vSignOut !== logout) return window.vSignOut();
        return clearSessionAndRedirect();
    }

    if (!window.vSignOut) window.vSignOut = clearSessionAndRedirect;

    const schools = {
        allSchools: [], pendingDeleteId: null, pendingDeleteType: null,
        async init() {
            try {
                this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
                document.getElementById('schoolSearch').addEventListener('input', (e) => {
                    this.applySearch(e.target.value);
                });
                const openBtn = document.getElementById('btnOpenLedger');
                openBtn.addEventListener('click', () => {
                    if (!state.selectedSchool) return ui.showToast('Please select a school first.', 'error');
                    if (!state.selectedClass) return ui.showToast('Please select a class first.', 'error');
                    window.location.href = 'ledger.html';
                });
                // Re-enable the button so a failed submit can be retried.
                openBtn.disabled = false;
                openBtn.classList.add('is-locked');

                // Handle ?q= (search redirected from global search) and ?add=1 (open Add School modal).
                try {
                    const params = new URLSearchParams(window.location.search);
                    const q = params.get('q');
                    if (q) this.applySearch(q);
                    if (params.get('add') === '1' && state.role === 'admin') {
                        ui.openModal('modalAddSchool');
                    }
                } catch (e) {}
            } catch (e) { document.getElementById('schoolList').innerHTML = '<li class="list-item text-danger">Failed to load schools</li>'; }
        },
        classMatches(c, term) {
            const compact = String(term || '').toLowerCase().replace(/\s+/g, '');
            const name = String(c?.name || '').toLowerCase();
            const nameCompact = name.replace(/\s+/g, '');
            return name.includes(String(term || '').toLowerCase()) || (!!compact && nameCompact.includes(compact));
        },
        async applySearch(term) {
            const box = document.getElementById('schoolSearch');
            if (box) box.value = term;
            const raw = String(term || '').trim();
            const t = raw.toLowerCase();
            if (!t) {
                this.renderSchools(this.allSchools);
                document.getElementById('classSelectionArea').innerHTML = '<div style="text-align: center; color: var(--text-muted); margin-top: 2rem;">Please select a school from the left panel first.</div>';
                document.getElementById('selectionSummary').innerHTML = 'No class selected.';
                return;
            }
            const schoolMatches = this.allSchools.filter(s =>
                (s.name || '').toLowerCase().includes(t) ||
                (s.code || '').toLowerCase().includes(t) ||
                (s.location || '').toLowerCase().includes(t)
            );
            this.renderSchools(schoolMatches);
            await this.searchClassesEverywhere(raw, schoolMatches.length > 0);
        },
        async searchClassesEverywhere(term, hasSchoolMatches) {
            const classArea = document.getElementById('classSelectionArea');
            if (!classArea) return;
            classArea.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">Searching classes in every school...</div>';
            try {
                const batches = await Promise.all(this.allSchools.map(async (s) => {
                    try {
                        const classes = await apiCall(`/schools/${s.id}/classes`);
                        return classes
                            .filter(c => this.classMatches(c, term))
                            .map(c => ({ school: s, classInfo: c }));
                    } catch (e) { return []; }
                }));
                const matches = batches.flat();
                if (!matches.length) {
                    classArea.innerHTML = `<div style="padding:20px; text-align:center; color:var(--text-muted);">${hasSchoolMatches ? 'No matching classes found.' : 'No schools or classes match your search.'}</div>`;
                    return;
                }
                classArea.innerHTML = `<ul class="list-group">${matches.map(m => `
                    <li class="list-item class-search-result" style="display:flex; justify-content:space-between; align-items:center;" onclick="App.schools.selectClassSearch(${m.school.id}, '${ui.escapeHTML(m.school.name)}', ${m.classInfo.id}, '${ui.escapeHTML(m.classInfo.name)}', ${parseInt(m.classInfo.strength) || 0}, this)">
                        <span><strong>${ui.escapeHTML(m.classInfo.name)}</strong><br><small style="color:var(--text-muted);">${ui.escapeHTML(m.school.name)} • Strength: <strong>${parseInt(m.classInfo.strength) || 0}</strong></small></span>
                    </li>
                `).join('')}</ul>`;
            } catch (e) {
                classArea.innerHTML = '<div class="text-danger" style="padding:20px;">Failed to search classes.</div>';
            }
        },
        renderSchools(list) {
            document.getElementById('schoolList').innerHTML = list.map(s => `
                <li class="list-item" style="display:flex; justify-content:space-between; align-items:center;" onclick="App.schools.selectSchool(${s.id}, '${ui.escapeHTML(s.name)}')">
                    <div><strong>${ui.escapeHTML(s.name)}</strong><br><small style="color:var(--text-muted)">Code: ${s.code || 'N/A'} | ${s.location || 'N/A'}</small></div>
                    <button class="icon-btn admin-only" onclick="event.stopPropagation(); App.schools.confirmDelete('school', ${s.id})">✖</button>
                </li>
            `).join('');
            if(state.role !== 'admin') document.querySelectorAll('.admin-only').forEach(el => el.style.display = 'none');
        },
        async selectSchool(id, name) {
            document.querySelectorAll('#schoolList .list-item').forEach(el => el.classList.remove('active')); event.currentTarget.classList.add('active');
            state.selectedSchool = { id, name }; localStorage.setItem('v_school', JSON.stringify(state.selectedSchool));
            const btnAddClass = document.getElementById('btnAddClass'); if(btnAddClass) btnAddClass.disabled = false;
            await this.loadClassesForSchool(id);
        },
        async submitAddSchool(e) {
            e.preventDefault(); const btn = document.getElementById('btnSaveSchool'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                await apiCall('/schools', 'POST', { name: document.getElementById('newSchoolName').value, code: document.getElementById('newSchoolCode').value, location: document.getElementById('newSchoolLocation').value });
                ui.showToast('School created successfully.', 'success'); ui.closeModal('modalAddSchool');
                this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Save School'; }
        },
        async loadClassesForSchool(id) {
            const classArea = document.getElementById('classSelectionArea'); classArea.innerHTML = '<div style="padding:20px;">Loading classes...</div>';
            try {
                const classes = await apiCall(`/schools/${id}/classes`);
                if(classes.length === 0) { classArea.innerHTML = '<div style="padding:20px; text-align:center;">No classes found.</div>'; return; }
                classArea.innerHTML = `<ul class="list-group">${classes.map(c => `
                    <li class="list-item" style="display:flex; justify-content:space-between; align-items:center;" onclick="App.schools.selectClass(${c.id}, '${c.name}', ${c.strength}, this)">
                        <span>${c.name} <br><small style="color:var(--text-muted);">Strength: <strong>${c.strength}</strong></small></span>
                        <button class="icon-btn admin-only" onclick="event.stopPropagation(); App.schools.confirmDelete('class', ${c.id})">✖</button>
                    </li>
                `).join('')}</ul>`;
                if(state.role !== 'admin') document.querySelectorAll('.admin-only').forEach(el => el.style.display = 'none');
            } catch (e) { classArea.innerHTML = '<div class="text-danger">Failed to load classes.</div>'; }
        },
        async submitAddClass(e) {
            e.preventDefault(); if (!state.selectedSchool) return;
            const btn = document.getElementById('btnSaveClass'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                await apiCall(`/schools/${state.selectedSchool.id}/classes`, 'POST', { name: document.getElementById('newClassName').value, strength: parseInt(document.getElementById('newClassStrength').value) });
                ui.showToast('Class added successfully.', 'success'); ui.closeModal('modalAddClass');
                await this.loadClassesForSchool(state.selectedSchool.id);
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Save Class'; }
        },
        selectClassSearch(schoolId, schoolName, classId, className, strength, element) {
            state.selectedSchool = { id: schoolId, name: schoolName };
            localStorage.setItem('v_school', JSON.stringify(state.selectedSchool));
            this.selectClass(classId, className, strength, element);
        },
        selectClass(id, name, strength, element) {
            document.querySelectorAll('#classSelectionArea .list-item').forEach(el => el.classList.remove('active')); element.classList.add('active');
            state.selectedClass = { id, name, strength }; localStorage.setItem('v_class', JSON.stringify(state.selectedClass));
            document.getElementById('selectionSummary').innerHTML = `<strong>Ready:</strong> ${state.selectedSchool.name} <br>Class: ${name} (Strength: ${strength})`;
            const openBtn = document.getElementById('btnOpenLedger');
            openBtn.disabled = false;
            openBtn.classList.remove('is-locked');
        },
        confirmDelete(type, id) {
            this.pendingDeleteType = type; this.pendingDeleteId = id;
            document.getElementById('lblDeleteType').textContent = type;
            ui.openModal('modalDeleteStruct');
        },
        async executeDeleteStruct() {
            try {
                if (this.pendingDeleteType === 'school') {
                    await apiCall(`/schools/${this.pendingDeleteId}`, 'DELETE');
                    this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
                    if(state.selectedSchool && state.selectedSchool.id === this.pendingDeleteId) { document.getElementById('classSelectionArea').innerHTML = ''; document.getElementById('btnOpenLedger').disabled = true; }
                } else {
                    await apiCall(`/schools/${state.selectedSchool.id}/classes/${this.pendingDeleteId}`, 'DELETE');
                    await this.loadClassesForSchool(state.selectedSchool.id);
                }
                ui.showToast(`${this.pendingDeleteType} deleted.`, 'success');
                ui.closeModal('modalDeleteStruct');
            } catch (err) { ui.showToast(err.message, 'error'); ui.closeModal('modalDeleteStruct'); }
        }
    };

    const ledger = {
        schema: [
            { key: 'bookName', type: 'text' }, { key: 'subject', type: 'text' },
            { key: 'publication', type: 'text' }, { key: 'vendor', type: 'text' },
            { key: 'category', type: 'text' }, { key: 'invoiceRef', type: 'text' },
            { key: 'strength', type: 'number' }, { key: 'purchased', type: 'number' },
            { key: 'booksRequired', type: 'readonly' }, { key: 'distributed', type: 'number' },
            { key: 'returned', type: 'number' }, { key: 'balance', type: 'readonly' }, { key: 'remarks', type: 'text' }
        ],
        async init() {
            if (!state.selectedSchool || !state.selectedClass) { window.location.href = 'schools.html'; return; }
            document.getElementById('lblSchoolName').textContent = state.selectedSchool.name;
            document.getElementById('lblClassName').textContent = state.selectedClass.name;
            const strLbl = document.getElementById('lblClassStrength');
            if (strLbl) strLbl.textContent = `Strength: ${state.selectedClass.strength} Students`;
            await this.fetchData();
            document.getElementById('ledgerSearch').addEventListener('input', (e) => this.filterTable(e.target.value));
            document.getElementById('ledgerTable').addEventListener('keydown', this.handleKeydown.bind(this));
            document.getElementById('ledgerTable').addEventListener('focusout', (e) => {
                if (!e.target.classList.contains('editable')) return;
                const tr = e.target.closest('tr');
                if (tr && tr.dataset.id) this.cellEdited(tr.dataset.id, e.target);
            });
            document.getElementById('ledgerTable').addEventListener('click', (e) => {
                const tr = e.target.closest('#ledgerBody tr');
                if (tr && tr.dataset.id) this.selectRow(tr.dataset.id);
            });
            document.getElementById('ledgerTable').addEventListener('focusin', (e) => {
                const tr = e.target.closest('#ledgerBody tr');
                if (tr && tr.dataset.id) this.selectRow(tr.dataset.id);
            });
        },
        async fetchData() {
            try {
                state.ledgerData = await apiCall(`/ledger/${state.selectedSchool.id}/${state.selectedClass.id}`);
                state.dirtyRecords.clear(); state.deletedIds.clear(); this.updateSaveButton(); this.render();
            } catch(e) { ui.showToast('Failed to load ledger records.', 'error'); }
        },
        render() {
            const tbody = document.getElementById('ledgerBody'); tbody.innerHTML = '';
            state.ledgerData.forEach((row, index) => {
                if(row._deleted) return;
                const tr = document.createElement('tr'); tr.dataset.id = row.id;
                let html = `<td>${index + 1}</td>`;
                this.schema.forEach(col => {
                    const val = (col.key === 'strength')
                        ? (row.strength !== undefined && row.strength !== null && row.strength !== '' ? row.strength : state.selectedClass.strength)
                        : row[col.key] ?? '';
                    const isDirty = state.dirtyRecords.has(row.id) && state.dirtyRecords.get(row.id)[col.key] !== undefined;
                    const dirtyClass = isDirty ? 'dirty' : '';
                    const alignClass = ['number', 'strength', 'booksRequired', 'balance'].includes(col.type) || col.key === 'balance' || col.key === 'booksRequired' || col.key === 'strength' ? 'col-num' : '';
                   
                    if (col.type === 'readonly') {
                        let c = '';
                        if(col.key === 'balance') c = parseInt(val) <= 0 ? 'text-danger' : 'text-success';
                        if(col.key === 'booksRequired' && parseInt(val) > 0) c = 'text-warning';
                        html += `<td data-key="${col.key}" class="${alignClass} ${c}">${val}</td>`;
                    } else {
                        html += `<td class="editable ${alignClass} ${dirtyClass}" data-key="${col.key}" data-type="${col.type}" contenteditable="true">${ui.escapeHTML(val)}</td>`;
                    }
                });
                if (state.selectedRowId && String(row.id) === String(state.selectedRowId)) tr.classList.add('row-selected');
                tr.innerHTML = html;
                tbody.appendChild(tr);
            });
        },
        cellEdited(rowId, td) {
            const key = td.dataset.key;
            const type = td.dataset.type;
            let val = td.innerText.trim();

            if (type === 'number' || key === 'strength') val = Math.max(parseInt(val) || 0, 0);
            td.innerText = val;

            const row = state.ledgerData.find(r => r.id === rowId);
            if (!row) return;

            // Record the edited field for the pending save payload.
            row[key] = val;
            if (!state.dirtyRecords.has(rowId)) state.dirtyRecords.set(rowId, {});
            state.dirtyRecords.get(rowId)[key] = val;
            td.classList.add('dirty');

            // Recalculate the row balance and required-books figures.
            if (['purchased', 'distributed', 'returned', 'strength'].includes(key)) {
                const p = parseInt(row.purchased) || 0;
                const d = parseInt(row.distributed) || 0;
                const r = parseInt(row.returned) || 0;
                const s = parseInt(row.strength) || parseInt(state.selectedClass.strength) || 0;

                row.balance = p - d + r;
                row.booksRequired = Math.max(s - p, 0);

                const tr = td.parentElement;
                const balTd = tr.querySelector('td[data-key="balance"]');
                const reqTd = tr.querySelector('td[data-key="booksRequired"]');

                if(balTd) {
                    balTd.textContent = row.balance;
                    balTd.className = `col-num ${row.balance <= 0 ? 'text-danger' : 'text-success'}`;
                }
                if(reqTd) {
                    reqTd.textContent = row.booksRequired;
                    reqTd.className = `col-num ${row.booksRequired > 0 ? 'text-warning' : ''}`;
                }
            }
            this.updateSaveButton();
        },
        async downloadCSV() {
            try {
                ui.showToast('Generating CSV...', 'info');
                const res = await fetch(`${API_BASE_URL}/ledger/${state.selectedSchool.id}/${state.selectedClass.id}/download`, {
                    headers: { 'Authorization': `Bearer ${state.token}` }
                });
                if (!res.ok) throw new Error("Download failed");
               
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
               
                const contentDisposition = res.headers.get('Content-Disposition');
                let filename = 'Ledger.csv';
                if (contentDisposition && contentDisposition.includes('filename=')) {
                    filename = contentDisposition.split('filename=')[1].replace(/["']/g, '');
                }
               
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
                ui.showToast('Download complete.', 'success');
            } catch(e) {
                ui.showToast('Failed to download CSV.', 'error');
            }
        },
        handleKeydown(e) {
            if (!e.target.classList.contains('editable')) return;
            const td = e.target; const tr = td.parentElement;
            if (e.key === 'Enter') {
                e.preventDefault(); td.blur();
                const nextTr = tr.nextElementSibling;
                if (nextTr) { const nextTd = nextTr.children[Array.from(tr.children).indexOf(td)]; if (nextTd && nextTd.classList.contains('editable')) nextTd.focus(); }
            }
        },
        addRecord() {
            const newId = 'new_' + Date.now();
            const defaultStrength = state.selectedClass.strength;
            const newRow = {
                id: newId,
                bookName: 'New Book',
                strength: defaultStrength,
                purchased: 0,
                distributed: 0,
                returned: 0,
                balance: 0,
                booksRequired: defaultStrength,
                _isNew: true
            };
            state.ledgerData.unshift(newRow);
            state.dirtyRecords.set(newId, { bookName: 'New Book', strength: defaultStrength, purchased: 0, distributed: 0, returned: 0 });
            this.render();
            this.updateSaveButton();
        },
        // Highlights the row the user is working on; the Delete button acts on it.
        selectRow(id) {
            state.selectedRowId = String(id);
            document.querySelectorAll('#ledgerBody tr').forEach(tr => {
                tr.classList.toggle('row-selected', String(tr.dataset.id) === state.selectedRowId);
            });
        },
        // Toolbar "Delete" button: removes the currently selected row.
        deleteSelected() {
            const id = state.selectedRowId;
            const exists = id && state.ledgerData.some(r => String(r.id) === String(id) && !r._deleted);
            if (!exists) return ui.showToast('Click a row in the table first, then press Delete.', 'info');
            const row = state.ledgerData.find(r => String(r.id) === String(id));
            const name = (row && row.bookName) ? row.bookName : 'this row';
            if (!confirm(`Delete "${name}"? This is confirmed when you press Save Changes.`)) return;
            state.selectedRowId = null;
            this.deleteRecord(id);
        },
        // Removes the row from the grid; the deletion is persisted on Save.
        deleteRecord(id) {
            if (id === undefined || id === null || id === '') return;
            const sid = String(id);
            // IDs can arrive as numbers from the API but as strings from the DOM,
            // so always compare them as strings.
            if (sid.startsWith('new_')) {
                state.ledgerData = state.ledgerData.filter(r => String(r.id) !== sid);
                state.dirtyRecords.delete(sid);
                state.dirtyRecords.delete(id);
            } else {
                const row = state.ledgerData.find(r => String(r.id) === sid);
                if (row) row._deleted = true;
                state.deletedIds.add(sid);
            }
            this.render(); this.updateSaveButton();
            ui.showToast('Row removed. Press Save Changes to confirm.', 'info');
        },
        async saveChanges() {
            if (state.dirtyRecords.size === 0 && state.deletedIds.size === 0) return ui.showToast('No unsaved changes.', 'info');
            const btn = document.getElementById('btnSaveLedger'); btn.textContent = 'Saving...'; btn.disabled = true;
            try {
                await apiCall(`/ledger/sync`, 'POST', { schoolId: state.selectedSchool.id, classId: state.selectedClass.id, updates: Array.from(state.dirtyRecords.entries()).map(([id, changes]) => ({ id, ...changes })), deletes: Array.from(state.deletedIds) });
                ui.showToast('Changes saved successfully.', 'success'); await this.fetchData();
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { this.updateSaveButton(); }
        },
        updateSaveButton() {
            const btn = document.getElementById('btnSaveLedger'); if(!btn) return;
            const count = state.dirtyRecords.size + state.deletedIds.size;
            btn.textContent = `Save Changes (${count})`; btn.disabled = count === 0;
            btn.style.backgroundColor = count > 0 ? 'var(--warning)' : 'var(--primary-color)'; btn.style.borderColor = count > 0 ? 'var(--warning)' : 'var(--primary-color)';
        },
        filterTable(term) { term = term.toLowerCase(); document.querySelectorAll('#ledgerBody tr').forEach(row => { row.style.display = row.innerText.toLowerCase().includes(term) ? '' : 'none'; }); }
    };


    /* ---------------- User Administration (admin only) ---------------- */
    const users = {
        all: [], schools: [], editing: null,
        async init() {
            if (state.role !== 'admin') {
                document.getElementById('usersBody').innerHTML =
                    '<tr><td colspan="8" style="padding:20px;text-align:center;">Administrator access required.</td></tr>';
                const nb = document.getElementById('btnNewUser'); if (nb) nb.style.display = 'none';
                return;
            }
            try { this.schools = await apiCall('/schools'); } catch (e) { this.schools = []; }
            this.fillSchoolSelects();
            await this.load();
            const search = document.getElementById('userSearch');
            if (search) search.addEventListener('input', () => this.render());
            const filter = document.getElementById('userStatusFilter');
            if (filter) filter.addEventListener('change', () => this.render());
        },
        fillSchoolSelects() {
            const opts = '<option value="">— No specific school (all access) —</option>' +
                this.schools.map(s => `<option value="${s.id}">${ui.escapeHTML(s.name)}</option>`).join('');
            ['newUserSchool', 'editUserSchool'].forEach(id => {
                const el = document.getElementById(id); if (el) el.innerHTML = opts;
            });
        },
        async load() {
            try { this.all = await apiCall('/users'); this.render(); }
            catch (e) {
                document.getElementById('usersBody').innerHTML =
                    `<tr><td colspan="8" style="padding:20px;text-align:center;" class="text-danger">${ui.escapeHTML(e.message)}</td></tr>`;
            }
        },
        visible() {
            const term = (document.getElementById('userSearch')?.value || '').toLowerCase();
            const status = document.getElementById('userStatusFilter')?.value || '';
            return this.all.filter(u => {
                const hay = `${u.username} ${u.fullName} ${u.email} ${u.school_name} ${u.role}`.toLowerCase();
                return hay.includes(term) && (!status || u.status === status);
            });
        },
        badge(text, cls) { return `<span class="badge ${cls}">${ui.escapeHTML(text)}</span>`; },
        render() {
            const list = this.visible();
            const pending = this.all.filter(u => u.status === 'Pending').length;
            const banner = document.getElementById('pendingBanner');
            if (banner) {
                banner.style.display = pending ? '' : 'none';
                banner.textContent = `${pending} account request${pending === 1 ? '' : 's'} waiting for approval.`;
            }
            const body = document.getElementById('usersBody');
            if (!list.length) { body.innerHTML = '<tr><td colspan="8" style="padding:20px;text-align:center;">No users match your filters.</td></tr>'; return; }
            body.innerHTML = list.map(u => {
                const statusCls = u.status === 'Active' ? 'badge-active' : (u.status === 'Pending' ? 'badge-pending' : 'badge-disabled');
                const roleCls = u.role === 'admin' ? 'badge-admin' : 'badge-role';
                const onlineCls = u.online ? 'badge-online' : 'badge-offline';
                const uname = ui.escapeHTML(u.username);
                const unameAttr = ui.escapeHTML(u.username);
                let actions = '';
                if (u.status === 'Pending') actions += `<button class="btn-mini approve" data-act="status" data-status="Active" data-user="${unameAttr}">Approve</button>`;
                if (u.status === 'Active' && u.username !== state.username) actions += `<button class="btn-mini" data-act="status" data-status="Disabled" data-user="${unameAttr}">Disable</button>`;
                if (u.status === 'Disabled') actions += `<button class="btn-mini approve" data-act="status" data-status="Active" data-user="${unameAttr}">Enable</button>`;
                actions += `<button class="btn-mini" data-act="edit" data-user="${unameAttr}">Edit</button>`;
                actions += `<button class="btn-mini" data-act="pwd" data-user="${unameAttr}">Password</button>`;
                if (u.username !== state.username) actions += `<button class="btn-mini danger" data-act="delete" data-user="${unameAttr}">Delete</button>`;

                return `<tr>
                    <td><strong>${uname}</strong><div class="muted-small">${ui.escapeHTML(u.fullName || '—')}</div></td>
                    <td>${this.badge(u.role, roleCls)}</td>
                    <td>${ui.escapeHTML(u.school_name || (u.school_id ? 'School #' + u.school_id : '—'))}</td>
                    <td>${ui.escapeHTML(u.email || '—')}</td>
                    <td>${ui.escapeHTML(u.lastLogin || 'Never')}</td>
                    <td>${this.badge(u.online ? 'Online' : 'Offline', onlineCls)}</td>
                    <td>${this.badge(u.status, statusCls)}</td>
                    <td class="user-actions-cell"><div class="user-actions">${actions}</div></td>
                </tr>`;
            }).join('');
            // Row actions are delegated once.
            // username contains characters that clash with attribute escaping,
            if (!body.dataset.bound) {
                body.dataset.bound = '1';
                body.addEventListener('click', (ev) => {
                    const b = ev.target.closest('button[data-act]');
                    if (!b) return;
                    const u = b.dataset.user;
                    const act = b.dataset.act;
                    if (act === 'status') this.setStatus(u, b.dataset.status);
                    else if (act === 'edit') this.openEdit(u);
                    else if (act === 'pwd') this.openPassword(u);
                    else if (act === 'delete') this.confirmDelete(u);
                });
            }
        },
        openCreate() { ui.openModal('modalNewUser'); },
        async submitCreate(e) {
            e.preventDefault();
            const btn = document.getElementById('btnSaveUser'); btn.disabled = true; btn.textContent = 'Creating...';
            try {
                await apiCall('/users', 'POST', {
                    username: document.getElementById('newUserName').value.trim(),
                    password: document.getElementById('newUserPassword').value,
                    role: document.getElementById('newUserRole').value,
                    fullName: document.getElementById('newUserFullName').value.trim(),
                    email: document.getElementById('newUserEmail').value.trim(),
                    school_id: document.getElementById('newUserSchool').value,
                    status: document.getElementById('newUserStatus').value
                });
                ui.showToast('User created.', 'success');
                ui.closeModal('modalNewUser');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Create User'; }
        },
        openEdit(username) {
            const u = this.all.find(x => x.username === username); if (!u) return;
            this.editing = username;
            document.getElementById('editUserTitle').textContent = `Edit ${username}`;
            document.getElementById('editUserFullName').value = u.fullName || '';
            document.getElementById('editUserEmail').value = u.email || '';
            document.getElementById('editUserRole').value = u.role;
            document.getElementById('editUserStatus').value = u.status;
            document.getElementById('editUserSchool').value = u.school_id || '';
            ui.openModal('modalEditUser');
        },
        async submitEdit(e) {
            e.preventDefault();
            const btn = document.getElementById('btnUpdateUser'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                await apiCall(`/users/${encodeURIComponent(this.editing)}`, 'PUT', {
                    fullName: document.getElementById('editUserFullName').value.trim(),
                    email: document.getElementById('editUserEmail').value.trim(),
                    role: document.getElementById('editUserRole').value,
                    status: document.getElementById('editUserStatus').value,
                    school_id: document.getElementById('editUserSchool').value
                });
                ui.showToast('User updated.', 'success');
                ui.closeModal('modalEditUser');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Save Changes'; }
        },
        async setStatus(username, status) {
            try {
                await apiCall(`/users/${encodeURIComponent(username)}`, 'PUT', { status });
                ui.showToast(`${username} is now ${status}.`, 'success');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); }
        },
        openPassword(username) {
            this.editing = username;
            document.getElementById('pwdUserTitle').textContent = `Current passwords are protected. Set a new password for ${username}, then use Show to reveal what you typed before saving.`;
            ['pwdNew', 'pwdConfirm'].forEach(id => {
                const f = document.getElementById(id); if (f) f.type = 'password';
            });
            ui.openModal('modalPassword');
        },
        togglePwdVisible(id, btn) {
            const f = document.getElementById(id); if (!f) return;
            if (f.type === 'password') { f.type = 'text'; if (btn) btn.textContent = 'Hide'; }
            else { f.type = 'password'; if (btn) btn.textContent = 'Show'; }
        },
        async submitPassword(e) {
            e.preventDefault();
            const p1 = document.getElementById('pwdNew').value;
            const p2 = document.getElementById('pwdConfirm').value;
            if (p1 !== p2) return ui.showToast('Passwords do not match.', 'error');
            const btn = document.getElementById('btnSavePwd'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                await apiCall(`/users/${encodeURIComponent(this.editing)}/password`, 'POST', { newPassword: p1 });
                ui.showToast('Password updated.', 'success');
                ui.closeModal('modalPassword');
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Update Password'; }
        },
        confirmDelete(username) {
            if (username === state.username) {
                return ui.showToast("You cannot delete the account you're signed in with. Sign in as another admin first.", 'error');
            }
            this.editing = username;
            document.getElementById('lblDeleteUser').textContent = username;
            ui.openModal('modalDeleteUser');
        },
        async executeDelete() {
            try {
                await apiCall(`/users/${encodeURIComponent(this.editing)}`, 'DELETE');
                ui.showToast('User deleted.', 'success');
                ui.closeModal('modalDeleteUser');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); ui.closeModal('modalDeleteUser'); }
        }
    };

    /* ---------------- Activity Log (admin only) ---------------- */
    const activity = {
        rows: [],
        async init() {
            if (state.role !== 'admin') {
                document.getElementById('auditBody').innerHTML =
                    '<tr><td colspan="5" style="padding:20px;text-align:center;">Administrator access required.</td></tr>';
                return;
            }
            await this.load();
            ['auditUser', 'auditAction'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.addEventListener('input', () => this.render());
                if (el && el.tagName === 'SELECT') el.addEventListener('change', () => this.render());
            });
        },
        async load() {
            try {
                this.rows = await apiCall('/audit?limit=500');
                this.render();
            } catch (e) {
                document.getElementById('auditBody').innerHTML =
                    `<tr><td colspan="5" style="padding:20px;text-align:center;" class="text-danger">${ui.escapeHTML(e.message)}</td></tr>`;
            }
        },
        render() {
            const u = (document.getElementById('auditUser')?.value || '').toLowerCase();
            const a = document.getElementById('auditAction')?.value || '';
            const list = this.rows.filter(r =>
                (!u || (r.username || '').toLowerCase().includes(u)) && (!a || r.action === a));
            const body = document.getElementById('auditBody');
            const count = document.getElementById('auditCount');
            if (count) count.textContent = `${list.length} entries`;
            if (!list.length) { body.innerHTML = '<tr><td colspan="5" style="padding:20px;text-align:center;">No activity recorded yet.</td></tr>'; return; }
            body.innerHTML = list.map(r => `<tr>
                <td style="white-space:nowrap;">${ui.escapeHTML(r.timestamp)}</td>
                <td><strong>${ui.escapeHTML(r.username)}</strong><div class="muted-small">${ui.escapeHTML(r.role || '')}</div></td>
                <td><span class="badge badge-role">${ui.escapeHTML(r.action)}</span></td>
                <td>${ui.escapeHTML(r.entity || '—')}${r.entity_id ? `<div class="muted-small">${ui.escapeHTML(r.entity_id)}</div>` : ''}</td>
                <td>${ui.escapeHTML(r.details || '')}</td>
            </tr>`).join('');
        },
        async download() {
            try {
                const res = await fetch(`${API_BASE_URL}/audit/download`, { headers: { 'Authorization': `Bearer ${state.token}` } });
                if (!res.ok) throw new Error('Download failed');
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url; link.download = 'Vedritam_Activity_Log.csv';
                document.body.appendChild(link); link.click(); link.remove();
                window.URL.revokeObjectURL(url);
            } catch (e) { ui.showToast('Failed to download the activity log.', 'error'); }
        }
    };

    async function init() {
        const ok = await checkAuth();
        if (!ok) return;
        const p = window.location.pathname;
        if (p.endsWith('index.html') || p === '/' || p === '') { auth.init(); }
        else if (p.endsWith('schools.html')) { schools.init(); }
        else if (p.endsWith('users.html')) { users.init(); }
        else if (p.endsWith('activity.html')) { activity.init(); }
        else if (p.endsWith('ledger.html')) { ledger.init(); }
    }

    // Tab exit warning when unsaved changes exist
    window.addEventListener('beforeunload', (e) => {
        if (state.dirtyRecords.size > 0 || state.deletedIds.size > 0) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    document.addEventListener('DOMContentLoaded', init);
    return { ui, state, logout, schools, ledger, login, auth, users, activity, apiCall, applyRoleVisibility };
})();
// Expose the app namespace to other scripts.
window.App = App;

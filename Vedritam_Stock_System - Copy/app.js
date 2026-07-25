/* app.js - Vedritam School Stock Ledger Management System */

const App = (function() {
    const USE_MOCK_API = false;
    const API_BASE_URL = '/api/v1';

    const state = {
        token: localStorage.getItem('v_token'), username: localStorage.getItem('v_username'), role: localStorage.getItem('v_role'),
        selectedSchool: JSON.parse(localStorage.getItem('v_school') || 'null'),
        selectedClass: JSON.parse(localStorage.getItem('v_class') || 'null'),
        ledgerData: [], dirtyRecords: new Map(), deletedIds: new Set()
    };

    const ui = {
        showToast(message, type = 'success') {
            const container = document.getElementById('toast-container');
            if (!container) return;
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
        try {
            const res = await fetch(`${API_BASE_URL}${endpoint}`, { method, headers, body: body ? JSON.stringify(body) : null });
            if (res.status === 401) { logout(); throw new Error("Session expired."); }
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            return data;
        } catch (error) { throw error; }
    }

    function checkAuth() {
        const path = window.location.pathname;
        const isIndex = path.endsWith('index.html') || path === '/' || path === '';
        if (!state.token && !isIndex) window.location.href = 'index.html';
        else if (state.token && isIndex) window.location.href = 'dashboard.html';
        if (state.role !== 'admin') document.querySelectorAll('.admin-only').forEach(el => el.style.display = 'none');
        const userLbl = document.getElementById('navUsername'); if (userLbl) userLbl.textContent = state.username || 'User';
    }

    async function login(e) {
        e.preventDefault();
        const btn = document.getElementById('loginBtn'); btn.textContent = 'Authenticating...'; btn.disabled = true;
        try {
            const res = await apiCall('/auth/login', 'POST', { username: document.getElementById('username').value, password: document.getElementById('password').value });
            localStorage.setItem('v_token', res.access_token); localStorage.setItem('v_username', res.username); localStorage.setItem('v_role', res.role);
            window.location.href = 'dashboard.html';
        } catch (error) { ui.showToast(error.message, 'error'); btn.textContent = 'Authenticate'; btn.disabled = false; }
    }
    function logout() { localStorage.clear(); window.location.href = 'index.html'; }

    const schools = {
        allSchools: [], pendingDeleteId: null, pendingDeleteType: null,
        async init() {
            try {
                this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
                document.getElementById('schoolSearch').addEventListener('input', (e) => {
                    const term = e.target.value.toLowerCase();
                    this.renderSchools(this.allSchools.filter(s => s.name.toLowerCase().includes(term) || s.code.toLowerCase().includes(term)));
                });
                document.getElementById('btnOpenLedger').addEventListener('click', () => { window.location.href = 'ledger.html'; });
            } catch (e) { document.getElementById('schoolList').innerHTML = '<li class="list-item text-danger">Failed to load schools</li>'; }
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
        selectClass(id, name, strength, element) {
            document.querySelectorAll('#classSelectionArea .list-item').forEach(el => el.classList.remove('active')); element.classList.add('active');
            state.selectedClass = { id, name, strength }; localStorage.setItem('v_class', JSON.stringify(state.selectedClass));
            document.getElementById('selectionSummary').innerHTML = `<strong>Ready:</strong> ${state.selectedSchool.name} <br>Class: ${name} (Strength: ${strength})`;
            document.getElementById('btnOpenLedger').disabled = false;
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
                html += `<td class="col-action"><button class="icon-btn" onclick="App.ledger.confirmDelete('${row.id}')">✖</button></td>`;
                tr.innerHTML = html;
                tr.querySelectorAll('.editable').forEach(td => td.addEventListener('blur', (e) => this.cellEdited(row.id, e.target)));
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

            // Record changes
            row[key] = val;
            if (!state.dirtyRecords.has(rowId)) state.dirtyRecords.set(rowId, {});
            state.dirtyRecords.get(rowId)[key] = val;
            td.classList.add('dirty');

            // Recalculate row-specific balance & books required
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
        confirmDelete(id) { state.pendingDelete = id; ui.openModal('confirmModal'); },
        executeDelete() {
            const id = state.pendingDelete;
            if (id.startsWith('new_')) { state.ledgerData = state.ledgerData.filter(r => r.id !== id); state.dirtyRecords.delete(id); }
            else { const row = state.ledgerData.find(r => r.id === id); if (row) row._deleted = true; state.deletedIds.add(id); }
            ui.closeModal('confirmModal'); this.render(); this.updateSaveButton();
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

    function init() {
        checkAuth(); const p = window.location.pathname;
        if (p.endsWith('index.html') || p === '/' || p === '') { const f = document.getElementById('loginForm'); if(f) f.addEventListener('submit', login); }
        else if (p.endsWith('schools.html')) { schools.init(); }
        else if (p.endsWith('ledger.html')) { ledger.init(); document.getElementById('btnConfirmDelete').addEventListener('click', () => ledger.executeDelete()); }
    }

    // Tab exit warning when unsaved changes exist
    window.addEventListener('beforeunload', (e) => {
        if (state.dirtyRecords.size > 0 || state.deletedIds.size > 0) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    document.addEventListener('DOMContentLoaded', init);
    return { ui, logout, schools, ledger, login };
})();
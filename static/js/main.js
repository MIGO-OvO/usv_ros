
import { createApp, reactive, computed, toRefs } from '/static/js/lib/vue.esm-browser.prod.js';
import AppHeader from '/static/js/components/AppHeader.js';
import DashboardView from '/static/js/components/DashboardView.js';
import ConfigView from '/static/js/components/ConfigView.js';
import LogView from '/static/js/components/LogView.js';
import ManualControlView from '/static/js/components/ManualControlView.js';
import PIDConfigView from '/static/js/components/PIDConfigView.js';
import CalibrationPanel from '/static/js/components/CalibrationPanel.js';

// Icons (SVG Strings)
const Icons = {
    dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>',
    manual: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polygon points="10,8 16,12 10,16"></polygon></svg>',
    pid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22,12 18,12 15,21 9,3 6,12 2,12"></polyline></svg>',
    config: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>',
    logs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>'
};

const app = createApp({
    components: { AppHeader, DashboardView, ConfigView, LogView, ManualControlView, PIDConfigView, CalibrationPanel },
    setup() {
        // --- State ---
        const state = reactive({
            views: [
                { id: 'dashboard', label: '概览', icon: Icons.dashboard },
                { id: 'manual', label: '手动', icon: Icons.manual },
                { id: 'pid', label: 'PID', icon: Icons.pid },
                { id: 'config', label: '配置', icon: Icons.config },
                { id: 'logs', label: '日志', icon: Icons.logs }
            ],
            currentView: 'dashboard',

            // Connection
            connection: {
                socket: false,
                pump: false,
                automation: false
            },

            // Data
            angles: { X: 0, Y: 0, Z: 0, A: 0 },

            // Logs
            logs: [],

            // Config Data
            config: {
                mission: { name: '' },
                pump_settings: { pid_mode: true, pid_precision: 0.1 },
                sampling_sequence: { loop_count: 1, steps: [] }
            },

            // Presets List
            presets: [],

            // Helper
            toasts: []
        });

        const isConfigDirty = reactive({ value: false });

        // --- Socket.IO ---
        let socket = null;
        if (typeof io !== 'undefined') {
            socket = io();
        } else {
            console.error("Socket.IO client library not loaded!");
            // Note: Cannot call addToast here as it's defined later
        }

        if (socket) {
            socket.on('connect', () => {
                state.connection.socket = true;
                addToast('已连接服务器', 'success');
            });

            socket.on('disconnect', () => {
                state.connection.socket = false;
                addToast('连接断开', 'error');
            });

            socket.on('status', (data) => {
                state.connection.pump = data.pump_connected;
                state.connection.automation = data.automation_running;
            });

            socket.on('angles', (data) => {
                Object.assign(state.angles, data);
            });

            socket.on('log', (data) => {
                const entry = {
                    id: Date.now() + Math.random(),
                    time: data.timestamp,
                    msg: data.message,
                    level: data.level || 'info'
                };
                state.logs.unshift(entry);
                if (state.logs.length > 200) state.logs.pop();
            });
        }

        // --- Methods ---
        const addToast = (message, type = 'info') => {
            const id = Date.now();
            state.toasts.push({ id, message, type });
            setTimeout(() => {
                const idx = state.toasts.findIndex(t => t.id === id);
                if (idx > -1) state.toasts.splice(idx, 1);
            }, 3000);
        };

        const fetchConfig = async () => {
            try {
                const r = await fetch('/api/config');
                const data = await r.json();
                state.config = data;
                isConfigDirty.value = false;
            } catch (e) {
                addToast('配置加载失败', 'error');
            }
        };

        const saveConfig = async (newConfig) => {
            try {
                const r = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(newConfig)
                });
                const d = await r.json();
                if (d.success) {
                    addToast('配置已保存', 'success');
                    state.config = newConfig; // Sync
                    isConfigDirty.value = false;
                } else {
                    addToast(d.message, 'error');
                }
            } catch (e) {
                addToast('保存失败', 'error');
            }
        };

        const resetConfig = async () => {
            if (!confirm('确定重置为默认配置?')) return;
            await fetch('/api/config/reset', { method: 'POST' });
            fetchConfig();
            addToast('配置已重置', 'warning');
        };

        // Mission Control
        const handleMissionControl = async (action) => {
            try {
                const r = await fetch(`/api/mission/${action}`, { method: 'POST' });
                const d = await r.json();
                addToast(d.message, d.success ? 'success' : 'error');
            } catch (e) {
                addToast('命令发送失败', 'error');
            }
        };
        const stopMission = () => handleMissionControl('stop');

        // Presets
        const fetchPresets = async () => {
            const r = await fetch('/api/presets/auto');
            const d = await r.json();
            if (d.success) state.presets = d.data;
        };

        const loadPreset = async (name) => {
            const r = await fetch(`/api/preset/auto/${name}`);
            const d = await r.json();
            if (d.success) {
                state.config.sampling_sequence.steps = d.data.steps;
                state.config.sampling_sequence.loop_count = d.data.loop_count;
                addToast(`预设 ${name} 已加载`, 'success');
                isConfigDirty.value = true;
            }
        };

        const savePreset = async (data) => {
            const { name, steps, loop_count } = data;
            const r = await fetch(`/api/preset/auto/${name}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steps, loop_count })
            });
            const d = await r.json();
            if (d.success) {
                addToast('预设已保存', 'success');
                fetchPresets();
            }
        };

        const deletePreset = async (name) => {
            await fetch(`/api/preset/auto/${name}`, { method: 'DELETE' });
            addToast('预设已删除', 'warning');
            fetchPresets();
        };

        const clearLogs = () => {
            state.logs = [];
        };

        // Init
        fetchConfig();
        fetchPresets();

        return {
            ...toRefs(state),
            status: state.connection, // Alias for component compatibility
            isConfigDirty,
            addToast,
            saveConfig,
            resetConfig,
            handleMissionControl,
            stopMission,
            clearLogs,
            loadPreset,
            savePreset,
            deletePreset
        };
    }
});

app.mount('#app');

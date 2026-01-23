
import { ref, reactive, onMounted } from '/static/js/lib/vue.esm-browser.prod.js';

export default {
    props: ['angles'],
    emits: ['toast'],
    template: `
    <div class="calibration-panel glass-panel">
        <div class="glass-header">
            <div class="glass-title">零点标定</div>
            <button class="btn btn-sm btn-danger" @click="resetAll">重置所有</button>
        </div>
        <div class="glass-body">
            <div class="calibration-grid">
                <div v-for="motor in ['X', 'Y', 'Z', 'A']" :key="motor" class="calibration-item">
                    <div class="motor-label">电机 {{ motor }}</div>
                    <div class="current-angle">{{ (angles[motor] || 0).toFixed(2) }}°</div>
                    <div class="offset-display" :class="{ active: offsets[motor] !== 0 }">
                        偏移: {{ offsets[motor].toFixed(2) }}°
                    </div>
                    <button class="btn btn-sm btn-primary" @click="setZero(motor)">设为零点</button>
                </div>
            </div>
            
            <div class="calibration-actions">
                <button class="btn btn-warning" @click="startCalibration">
                    <svg viewBox="0 0 24 24" fill="currentColor" width="16">
                        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                    </svg>
                    启动校准
                </button>
            </div>
        </div>
    </div>

    <style>
    .calibration-panel { margin-bottom: var(--spacing-lg); }
    
    .calibration-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 16px;
    }
    
    @media (max-width: 768px) {
        .calibration-grid { grid-template-columns: repeat(2, 1fr); }
    }
    
    .calibration-item {
        text-align: center;
        padding: 12px;
        background: rgba(0,0,0,0.2);
        border-radius: var(--radius-sm);
        border: 1px solid rgba(255,255,255,0.05);
    }
    
    .motor-label {
        font-weight: bold;
        color: var(--color-text-muted);
        margin-bottom: 8px;
    }
    
    .current-angle {
        font-size: 1.5em;
        font-weight: 600;
        color: var(--color-primary);
        margin-bottom: 4px;
    }
    
    .offset-display {
        font-size: 0.85em;
        color: var(--color-text-muted);
        margin-bottom: 8px;
    }
    
    .offset-display.active {
        color: var(--color-warning);
    }
    
    .calibration-actions {
        margin-top: 16px;
        padding-top: 16px;
        border-top: 1px solid rgba(255,255,255,0.1);
    }
    </style>
    `,
    setup(props, { emit }) {
        const offsets = reactive({ X: 0, Y: 0, Z: 0, A: 0 });

        const loadOffsets = async () => {
            try {
                const res = await fetch('/api/calibration/offsets');
                const data = await res.json();
                if (data.success) {
                    Object.assign(offsets, data.data);
                }
            } catch (e) {
                console.error('Failed to load offsets:', e);
            }
        };

        const setZero = async (motor) => {
            try {
                const res = await fetch('/api/calibration/zero', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ motor })
                });
                const data = await res.json();
                if (data.success) {
                    offsets[motor] = data.offset;
                    emit('toast', data.message, 'success');
                } else {
                    emit('toast', data.message, 'error');
                }
            } catch (e) {
                emit('toast', '设置零点失败', 'error');
            }
        };

        const resetAll = async () => {
            try {
                const res = await fetch('/api/calibration/reset', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    Object.keys(offsets).forEach(k => offsets[k] = 0);
                    emit('toast', data.message, 'warning');
                }
            } catch (e) {
                emit('toast', '重置失败', 'error');
            }
        };

        const startCalibration = async () => {
            try {
                const res = await fetch('/api/calibration/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ motors: 'XYZA' })
                });
                const data = await res.json();
                emit('toast', data.message, data.success ? 'info' : 'error');
            } catch (e) {
                emit('toast', '启动校准失败', 'error');
            }
        };

        onMounted(() => {
            loadOffsets();
        });

        return {
            offsets,
            setZero,
            resetAll,
            startCalibration
        };
    }
};

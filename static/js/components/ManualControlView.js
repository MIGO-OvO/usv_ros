
import { ref, reactive, onMounted } from '/static/js/lib/vue.esm-browser.prod.js';

export default {
    props: ['connection'],
    emits: ['toast'],
    template: `
    <div class="manual-control-container">
        <!-- Motor Cards Grid -->
        <div class="motor-grid">
            <div v-for="motor in ['X', 'Y', 'Z', 'A']" :key="motor" class="glass-panel motor-card">
                <div class="glass-header">
                    <div class="glass-title">电机 {{ motor }}</div>
                    <label class="toggle-switch">
                        <input type="checkbox" 
                               v-model="motors[motor].enabled"
                               aria-label="启用电机">
                        <span class="slider" aria-hidden="true"></span>
                    </label>
                </div>
                <div class="glass-body" :class="{ disabled: !motors[motor].enabled }">
                    <!-- Direction -->
                    <div class="form-group">
                        <label>方向</label>
                        <div class="btn-group">
                            <button :class="['btn', 'btn-sm', motors[motor].direction === 'F' ? 'btn-primary' : '']" 
                                    @click="motors[motor].direction = 'F'">正转</button>
                            <button :class="['btn', 'btn-sm', motors[motor].direction === 'B' ? 'btn-primary' : '']" 
                                    @click="motors[motor].direction = 'B'">反转</button>
                        </div>
                    </div>
                    <!-- Speed -->
                    <div class="form-group">
                        <label :for="`motor-${motor}-speed`">速度 (RPM)</label>
                        <input :id="`motor-${motor}-speed`"
                               type="number" 
                               v-model.number="motors[motor].speed" 
                               class="input-glass" 
                               min="0" 
                               max="20" 
                               step="1"
                               aria-label="电机速度">
                    </div>
                    <!-- Angle -->
                    <div class="form-group">
                        <label :for="`motor-${motor}-angle`">角度 (°)</label>
                        <input :id="`motor-${motor}-angle`"
                               type="number" 
                               v-model.number="motors[motor].angle" 
                               class="input-glass" 
                               min="0" 
                               max="3600" 
                               step="1"
                               aria-label="电机角度">
                    </div>
                    <!-- Continuous -->
                    <div class="form-group">
                        <label>
                            <input type="checkbox" 
                                   v-model="motors[motor].continuous"
                                   aria-label="持续转动"> 持续转动
                        </label>
                    </div>
                </div>
            </div>
        </div>

        <!-- Control Buttons -->
        <div class="glass-panel control-panel">
            <div class="glass-body" style="display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">
                <button class="btn btn-success" @click="sendCommand" :disabled="!hasEnabledMotor || isLoading">
                    <svg v-if="!isLoading" viewBox="0 0 24 24" fill="currentColor" width="16"><polygon points="5,3 19,12 5,21"/></svg>
                    <svg v-else class="animate-spin" viewBox="0 0 24 24" fill="none" width="16">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" opacity="0.25"/>
                        <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" opacity="0.75"/>
                    </svg>
                    {{ isLoading ? '发送中...' : '发送指令' }}
                </button>
                <button class="btn btn-danger" @click="stopAll" :disabled="isLoading">
                    <svg v-if="!isLoading" viewBox="0 0 24 24" fill="currentColor" width="16"><rect x="6" y="6" width="12" height="12"/></svg>
                    <svg v-else class="animate-spin" viewBox="0 0 24 24" fill="none" width="16">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" opacity="0.25"/>
                        <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" opacity="0.75"/>
                    </svg>
                    {{ isLoading ? '停止中...' : '紧急停止' }}
                </button>
                
                <div class="divider-v"></div>
                
                <!-- Timed Run -->
                <div class="timed-run-group">
                    <span style="color: var(--color-text-muted);">定时运行:</span>
                    <input type="number" v-model.number="timedRun.duration" class="input-glass" style="width: 80px;" min="1" placeholder="时长">
                    <select v-model="timedRun.unit" class="input-glass" style="width: 80px;">
                        <option value="s">秒</option>
                        <option value="m">分钟</option>
                        <option value="h">小时</option>
                    </select>
                    <button class="btn btn-primary btn-sm" @click="startTimedRun" :disabled="timedRun.running">
                        {{ timedRun.running ? formatTime(timedRun.remaining) : '开始' }}
                    </button>
                    <button class="btn btn-sm" @click="cancelTimedRun" :disabled="!timedRun.running">取消</button>
                </div>
            </div>
        </div>

        <!-- Command Preview -->
        <div class="glass-panel">
            <div class="glass-header">
                <div class="glass-title">指令预览</div>
            </div>
            <div class="glass-body">
                <code class="command-preview">{{ commandPreview || '(无启用电机)' }}</code>
            </div>
        </div>
    </div>
    
    <style>
    .manual-control-container { max-width: 1200px; margin: 0 auto; }
    
    .motor-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(200px, 1fr));
        gap: var(--spacing-lg, 16px);
        margin-bottom: var(--spacing-lg, 16px);
    }
    
    .motor-card {
        min-width: 0;
    }
    
    @media (max-width: 520px) {
        .motor-grid { grid-template-columns: 1fr; }
    }
    
    .motor-card .glass-body.disabled {
        opacity: 0.4;
        pointer-events: none;
    }
    
    .form-group { margin-bottom: 12px; }
    .form-group label { display: block; color: var(--color-text-muted); margin-bottom: 4px; font-size: 0.9em; }
    
    .btn-group { display: flex; gap: 4px; }
    .btn-group .btn { flex: 1; }
    
    .toggle-switch {
        position: relative;
        width: 44px;
        height: 24px;
    }
    .toggle-switch input { opacity: 0; width: 0; height: 0; }
    .toggle-switch .slider {
        position: absolute;
        cursor: pointer;
        top: 0; left: 0; right: 0; bottom: 0;
        background-color: #555;
        transition: .3s;
        border-radius: 24px;
    }
    .toggle-switch .slider:before {
        position: absolute;
        content: "";
        height: 18px;
        width: 18px;
        left: 3px;
        bottom: 3px;
        background-color: white;
        transition: .3s;
        border-radius: 50%;
    }
    .toggle-switch input:checked + .slider { background-color: var(--color-primary); }
    .toggle-switch input:checked + .slider:before { transform: translateX(20px); }
    
    .control-panel { margin-bottom: var(--spacing-lg); }
    .divider-v { width: 1px; height: 30px; background: rgba(255,255,255,0.1); }
    
    .timed-run-group { display: flex; align-items: center; gap: 8px; }
    
    .command-preview {
        display: block;
        font-family: 'Fira Code', monospace;
        background: rgba(0,0,0,0.3);
        padding: 12px;
        border-radius: var(--radius-sm);
        color: var(--color-primary);
        word-break: break-all;
    }
    </style>
    `,
    setup(props, { emit }) {
        const motors = reactive({
            X: { enabled: false, direction: 'F', speed: 5, angle: 90, continuous: false },
            Y: { enabled: false, direction: 'F', speed: 5, angle: 90, continuous: false },
            Z: { enabled: false, direction: 'F', speed: 5, angle: 90, continuous: false },
            A: { enabled: false, direction: 'F', speed: 5, angle: 90, continuous: false }
        });

        const timedRun = reactive({
            duration: 10,
            unit: 's',
            running: false,
            remaining: 0
        });

        let timerInterval = null;

        const hasEnabledMotor = ref(false);
        const commandPreview = ref('');
        const isLoading = ref(false);  // 添加加载状态

        // Update command preview when motors change
        const updatePreview = () => {
            hasEnabledMotor.value = Object.values(motors).some(m => m.enabled);

            let cmd = '';
            for (const [motor, config] of Object.entries(motors)) {
                if (config.enabled) {
                    const angleStr = config.continuous ? 'G' : config.angle.toString();
                    cmd += `${motor}E${config.direction}V${config.speed}J${angleStr}`;
                }
            }
            commandPreview.value = cmd ? cmd + '\\r\\n' : '';
        };

        // Generate command string
        const generateCommand = () => {
            let cmd = '';
            for (const [motor, config] of Object.entries(motors)) {
                if (config.enabled) {
                    const angleStr = config.continuous ? 'G' : config.angle.toString();
                    cmd += `${motor}E${config.direction}V${config.speed}J${angleStr}`;
                }
            }
            return cmd ? cmd + '\r\n' : '';
        };

        const sendCommand = async () => {
            const command = generateCommand();
            if (!command) {
                emit('toast', '请至少启用一个电机', 'warning');
                return;
            }

            isLoading.value = true;  // 开始加载
            try {
                const res = await fetch('/api/motor/command', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command })
                });
                const data = await res.json();
                emit('toast', data.message, data.success ? 'success' : 'error');
            } catch (e) {
                const errorMsg = `发送失败: ${e.message || '网络错误'}`;
                emit('toast', errorMsg, 'error');
                console.error('[Motor Command Error]', e);
            } finally {
                isLoading.value = false;  // 结束加载
            }
        };

        const stopAll = async () => {
            isLoading.value = true;  // 开始加载
            try {
                const res = await fetch('/api/motor/stop', { method: 'POST' });
                const data = await res.json();
                emit('toast', data.message, data.success ? 'warning' : 'error');

                // Cancel timed run if active
                if (timedRun.running) {
                    cancelTimedRun();
                }
            } catch (e) {
                const errorMsg = `停止失败: ${e.message || '网络错误'}`;
                emit('toast', errorMsg, 'error');
                console.error('[Motor Stop Error]', e);
            } finally {
                isLoading.value = false;  // 结束加载
            }
        };

        const formatTime = (seconds) => {
            const m = Math.floor(seconds / 60);
            const s = seconds % 60;
            return `${m}:${s.toString().padStart(2, '0')}`;
        };

        const startTimedRun = async () => {
            if (!hasEnabledMotor.value) {
                emit('toast', '请至少启用一个电机', 'warning');
                return;
            }

            // Calculate duration in seconds
            let duration = timedRun.duration;
            if (timedRun.unit === 'm') duration *= 60;
            if (timedRun.unit === 'h') duration *= 3600;

            // Send command first
            await sendCommand();

            // Start timer
            timedRun.running = true;
            timedRun.remaining = duration;

            timerInterval = setInterval(() => {
                timedRun.remaining--;
                if (timedRun.remaining <= 0) {
                    cancelTimedRun();
                    stopAll();
                    emit('toast', '定时运行完成', 'success');
                }
            }, 1000);
        };

        const cancelTimedRun = () => {
            if (timerInterval) {
                clearInterval(timerInterval);
                timerInterval = null;
            }
            timedRun.running = false;
            timedRun.remaining = 0;
        };

        // Watch motors for preview update
        onMounted(() => {
            // Use a simple interval to update preview (Vue 3 watch can also be used)
            setInterval(updatePreview, 500);
        });

        return {
            motors,
            timedRun,
            hasEnabledMotor,
            commandPreview,
            isLoading,
            sendCommand,
            stopAll,
            startTimedRun,
            cancelTimedRun,
            formatTime
        };
    }
};

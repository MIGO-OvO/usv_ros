<template>
    <div class="pid-config-container">
        <!-- PID Parameters Card -->
        <div class="glass-panel">
            <div class="glass-header">
                <div class="glass-title">PID 参数配置</div>
            </div>
            <div class="glass-body">
                <div class="pid-params-grid">
                    <div class="form-group">
                        <label>Kp (比例系数)</label>
                        <input type="number" v-model.number="pidConfig.Kp" class="input-glass" step="0.01" min="0">
                    </div>
                    <div class="form-group">
                        <label>Ki (积分系数)</label>
                        <input type="number" v-model.number="pidConfig.Ki" class="input-glass" step="0.001" min="0">
                    </div>
                    <div class="form-group">
                        <label>Kd (微分系数)</label>
                        <input type="number" v-model.number="pidConfig.Kd" class="input-glass" step="0.01" min="0">
                    </div>
                    <div class="form-group">
                        <label>输出下限</label>
                        <input type="number" v-model.number="pidConfig.output_min" class="input-glass" step="0.1" min="0">
                    </div>
                    <div class="form-group">
                        <label>输出上限</label>
                        <input type="number" v-model.number="pidConfig.output_max" class="input-glass" step="0.1" min="0">
                    </div>
                </div>
                <div style="margin-top: 16px; display: flex; gap: 12px;">
                    <button class="btn btn-primary" @click="applyPidConfig">应用参数</button>
                    <button class="btn" @click="loadPidConfig">刷新</button>
                </div>
            </div>
        </div>

        <!-- PID Test Card -->
        <div class="glass-panel" style="margin-top: var(--spacing-lg);">
            <div class="glass-header">
                <div class="glass-title">PID 测试</div>
            </div>
            <div class="glass-body">
                <div class="test-params-grid">
                    <div class="form-group">
                        <label>电机</label>
                        <select v-model="testConfig.motor" class="input-glass">
                            <option value="X">X 轴</option>
                            <option value="Y">Y 轴</option>
                            <option value="Z">Z 轴</option>
                            <option value="A">A 轴</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>方向</label>
                        <select v-model="testConfig.direction" class="input-glass">
                            <option value="F">正转</option>
                            <option value="B">反转</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>目标角度 (°)</label>
                        <input type="number" v-model.number="testConfig.angle" class="input-glass" step="1" min="1" max="360">
                    </div>
                    <div class="form-group">
                        <label>测试次数</label>
                        <input type="number" v-model.number="testConfig.runs" class="input-glass" step="1" min="1" max="20">
                    </div>
                </div>
                <div style="margin-top: 16px;">
                    <button class="btn btn-success" @click="startTest" :disabled="testing">
                        {{ testing ? '测试中...' : '开始测试' }}
                    </button>
                </div>
            </div>
        </div>

        <!-- Command Preview -->
        <div class="glass-panel" style="margin-top: var(--spacing-lg);">
            <div class="glass-header">
                <div class="glass-title">指令预览</div>
            </div>
            <div class="glass-body">
                <code class="command-preview">PIDCFG:{{ pidConfig.Kp }},{{ pidConfig.Ki }},{{ pidConfig.Kd }},{{ pidConfig.output_min }},{{ pidConfig.output_max }}</code>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

const emit = defineEmits(['toast']);

const pidConfig = reactive({
    Kp: 0.14,
    Ki: 0.015,
    Kd: 0.06,
    output_min: 1.0,
    output_max: 8.0
});

const testConfig = reactive({
    motor: 'X',
    direction: 'F',
    angle: 90,
    runs: 5
});

const testing = ref(false);

const loadPidConfig = async () => {
    try {
        const res = await fetch('/api/pid/config');
        const data = await res.json();
        if (data.success) {
            Object.assign(pidConfig, data.data);
        }
    } catch (e) {
        emit('toast', '加载 PID 配置失败', 'error');
    }
};

const applyPidConfig = async () => {
    try {
        const res = await fetch('/api/pid/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(pidConfig)
        });
        const data = await res.json();
        emit('toast', data.message, data.success ? 'success' : 'error');
    } catch (e) {
        emit('toast', '应用 PID 配置失败', 'error');
    }
};

const startTest = async () => {
    testing.value = true;
    try {
        const res = await fetch('/api/pid/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(testConfig)
        });
        const data = await res.json();
        emit('toast', data.message, data.success ? 'success' : 'error');
    } catch (e) {
        emit('toast', '启动测试失败', 'error');
    }
    testing.value = false;
};

onMounted(() => {
    loadPidConfig();
});
</script>

<style scoped>
    .pid-config-container { 
        max-width: 800px; 
        margin: 0 auto;
        padding: 16px;
    }
    
    .pid-params-grid, .test-params-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 16px;
    }
    
    .form-group label {
        display: block;
        color: var(--color-text-muted, #888);
        margin-bottom: 4px;
        font-size: 0.9em;
    }
    
    .command-preview {
        display: block;
        font-family: 'Fira Code', monospace;
        background: rgba(0,0,0,0.3);
        padding: 12px;
        border-radius: var(--radius-sm, 4px);
        color: var(--color-primary, #00f3ff);
    }
    
    .input-glass {
        width: 100%;
        padding: 8px 12px;
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 4px;
        color: inherit;
        font-size: 1em;
    }
    
    .input-glass:focus {
        outline: none;
        border-color: var(--color-primary, #00f3ff);
    }
</style>
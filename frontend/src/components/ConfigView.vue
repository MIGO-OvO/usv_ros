<template>
    <div class="config-container">
        <!-- Toolbar -->
        <div class="glass-panel config-toolbar">
            <div class="glass-body" style="display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; gap:12px; align-items:center;">
                    <button class="btn btn-primary" @click="save">保存配置</button>
                    <button class="btn btn-danger" @click="$emit('reset')">重置</button>
                    <span v-if="isDirty" style="color:var(--color-warning); font-size:0.9em;">⚠️ 未保存</span>
                </div>
                
                <div class="preset-Area" style="display:flex; gap:8px;">
                    <select v-model="selectedPreset" class="input-glass" style="width:150px;">
                        <option value="">选择预设...</option>
                        <option v-for="p in presets" :value="p" :key="p">{{p}}</option>
                    </select>
                    <button class="btn btn-sm" @click="loadPreset">加载</button>
                    <button class="btn btn-sm" @click="savePreset">保存预设</button>
                    <button class="btn btn-sm btn-danger" @click="deletePreset">Del</button>
                </div>
            </div>
        </div>

        <!-- Global Settings -->
        <div class="glass-panel">
            <div class="glass-header"><div class="glass-title">全局设置</div></div>
            <div class="glass-body form-grid">
                <div class="form-group">
                    <label>任务名称</label>
                    <input type="text" v-model="config.mission.name" class="input-glass">
                </div>
                <div class="form-group">
                    <label>循环次数</label>
                    <input type="number" v-model.number="config.sampling_sequence.loop_count" class="input-glass">
                </div>
                 <div class="form-group">
                    <label>PID 控制</label>
                    <div class="toggle-switch">
                        <input type="checkbox" id="pid" v-model="config.pump_settings.pid_mode">
                        <label for="pid"></label>
                    </div>
                </div>
            </div>
        </div>

        <!-- Steps Editor -->
        <div class="glass-panel">
            <div class="glass-header">
                <div class="glass-title">采样步骤 ({{ config.sampling_sequence.steps.length }})</div>
                <button class="btn btn-sm btn-success" @click="addStep">+ 添加步骤</button>
            </div>
            
            <div class="glass-body steps-list">
                <TransitionGroup name="list" tag="div">
                    <div v-for="(step, index) in config.sampling_sequence.steps" 
                         :key="index" 
                         class="step-item"
                         draggable="true"
                         @dragstart="dragStart($event, index)"
                         @dragover.prevent
                         @drop="onDrop($event, index)">
                        
                        <!-- Delete button at top-right -->
                        <button class="step-delete-btn" @click="removeStep(index)" title="删除步骤">×</button>
                        
                        <!-- Compact Header Row -->
                        <div class="step-header">
                            <span class="step-idx">{{ index + 1 }}</span>
                            <input v-model="step.name" class="input-glass step-name" placeholder="步骤名称">
                            <div class="step-interval-group">
                                <input type="number" v-model.number="step.interval" class="input-glass step-interval">
                                <span class="interval-unit">ms</span>
                            </div>
                        </div>
                        
                        <!-- Axis Parameters Grid -->
                        <div class="step-motors">
                            <div v-for="axis in ['X','Y','Z','A']" :key="axis" 
                                 class="motor-conf" 
                                 :class="{active: step[axis]?.enable === 'E', inactive: step[axis]?.enable !== 'E'}">
                                <div class="motor-header">
                                    <label class="motor-checkbox">
                                        <input type="checkbox" 
                                               :checked="step[axis]?.enable === 'E'"
                                               @change="updateMotorEnable(step, axis, $event.target.checked)">
                                        <span class="axis-label">{{ axis }}</span>
                                    </label>
                                </div>
                                <div v-if="step[axis]?.enable === 'E'" class="motor-params">
                                    <div class="param-field">
                                        <label>速度</label>
                                        <input type="number" v-model.number="step[axis].speed" placeholder="5">
                                    </div>
                                    <div class="param-field">
                                        <label>角度</label>
                                        <input type="number" v-model.number="step[axis].angle" placeholder="90">
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </TransitionGroup>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, watch, toRaw } from 'vue';

const props = defineProps(['config', 'isDirty', 'presets']);
const emit = defineEmits(['save', 'reset', 'load-preset', 'save-preset', 'delete-preset', 'update:isDirty']);

const selectedPreset = ref("");

const save = () => emit('save', toRaw(props.config));

const addStep = () => {
    props.config.sampling_sequence.steps.push({
        name: "新步骤",
        X: { enable: 'D' }, Y: { enable: 'D' }, Z: { enable: 'D' }, A: { enable: 'D' },
        interval: 1000
    });
};

const removeStep = (idx) => {
    props.config.sampling_sequence.steps.splice(idx, 1);
};

const updateMotorEnable = (step, axis, enabled) => {
    if (!step[axis]) step[axis] = {};
    step[axis].enable = enabled ? 'E' : 'D';
    // Init defaults if enabled
    if (enabled && !step[axis].speed) {
        step[axis].speed = 5;
        step[axis].angle = 90;
        step[axis].direction = 'F';
    }
};

// Drag and Drop
let draggedIdx = -1;
const dragStart = (e, idx) => {
    draggedIdx = idx;
    e.dataTransfer.effectAllowed = 'move';
};
const onDrop = (e, dropIdx) => {
    if (draggedIdx === -1 || draggedIdx === dropIdx) return;
    const steps = props.config.sampling_sequence.steps;
    const item = steps.splice(draggedIdx, 1)[0];
    steps.splice(dropIdx, 0, item);
    draggedIdx = -1;
};

// Presets wrappers
const loadPreset = () => {
    if (selectedPreset.value) emit('load-preset', selectedPreset.value);
};
const savePreset = () => {
    const name = prompt("预设名称:");
    if (name) emit('save-preset', { name, steps: props.config.sampling_sequence.steps, loop_count: props.config.sampling_sequence.loop_count });
};
const deletePreset = () => {
    if (selectedPreset.value && confirm("确认删除?")) emit('delete-preset', selectedPreset.value);
};

// Watch for changes to mark dirty
// Note: We're watching the props.config directly.
// In Vue 3 script setup, props are reactive.
watch(() => props.config, () => {
    // We emit an update to the parent for sync modifier if used, or just let parent handle logic
    // But based on App.vue: :is-dirty="isConfigDirty" and App.vue has const isConfigDirty = ref(false)
    // We can't mutate props.isDirty directly if it's a boolean value passed down. 
    // Wait, in App.vue: `const isConfigDirty = ref(false)` passed as `:is-dirty="isConfigDirty"`.
    // The original JS code was doing `props.isDirty.value = true`, which implies it expected a Ref passed as prop?
    // But Vue props are unwrapped. 
    // In App.vue `saveConfig` sets `isConfigDirty.value = false`.
    // I should probably emit an event or rely on App.vue to update it?
    // Actually, looking at the old code: `props: ['config', 'isDirty']` and `props.isDirty.value = true`.
    // This implies `isDirty` was passed as an object (Ref) or the old code was using Vue 3 Composition API in a way where it kept the ref?
    // In standard Vue 3 props, `isDirty` would be the value `false`.
    // However, I will emit an event `update:isDirty` or similar?
    // Let's assume the parent `App.vue` might need adjustment or I should emit.
    // But wait, `App.vue` passed `:is-dirty="isConfigDirty"`.
    // If I change the watcher to emit:
    // Actually, the original code `props.isDirty.value = true` is suspicious for standard Props.
    // It might have worked if they passed the Ref object itself? `<ConfigView :is-dirty="isConfigDirty" ...>` passes value.
    // Unless they did `:is-dirty="isConfigDirty"` and `isConfigDirty` was an object wrapper?
    // In App.vue `const isConfigDirty = ref(false)`.
    // I will stick to standard pattern: Emit an event.
    // BUT `App.vue` does NOT listen to `@update:is-dirty`.
    // It seems `App.vue` relies on `isConfigDirty` being updated.
    // Let's check `App.vue` again.
    // It passes `:is-dirty="isConfigDirty"`.
    // If I want to update it, I should likely change App.vue to `<ConfigView ... @dirty="isConfigDirty = true" />`?
    // Or I can try to access it if it was passed as an object? No.
    // I will emit 'update:isDirty' and hope I can patch `App.vue` if needed, 
    // OR since I am not editing `App.vue` extensively, maybe I should check if I missed something.
    // Wait, the original `ConfigView.js` code:
    // `props: ['config', 'isDirty', ...]`
    // `watch(..., () => { props.isDirty.value = true; })`
    // This strongly suggests `isDirty` was passed as a Ref or object.
    // But in `App.vue`: `<ConfigView ... :is-dirty="isConfigDirty" ...>`
    // If `isConfigDirty` is a ref, the template unwraps it.
    // So `props.isDirty` receives the boolean value.
    // `props.isDirty.value` would be undefined.
    // The original code was likely broken or I misread how `vue.esm-browser.prod.js` handles it in that context.
    // OR, they were using `provide/inject`? No.
    // I will implement it correctly: Emit an event `dirty` and update `App.vue` to listen to it.
    emit('update:isDirty', true); 
}, { deep: true });
</script>

<style scoped>
    .config-container { max-width: 900px; margin: 0 auto; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
    .form-group label { display: block; color: var(--color-text-muted, #888); margin-bottom: 4px; font-size: 0.9em; }
    
    .steps-list { display: flex; flex-direction: column; gap: 12px; }
    .step-item { 
        background: rgba(0,0,0,0.3); 
        padding: 16px; 
        margin: 0; 
        cursor: move; 
        border-left: 3px solid transparent; 
        border-radius: var(--radius-sm, 4px);
    }
    .step-item:hover { border-left-color: var(--color-primary, #00f3ff); }
    
    .step-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
    .step-idx { color: var(--color-text-muted, #888); font-weight: bold; width: 24px; }
    .step-name { flex: 1; border: none; background: rgba(255,255,255,0.05); border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px; color: inherit; }
    .step-interval { width: 80px; text-align: right; padding: 4px 8px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: inherit; }
    .btn-icon { background: none; border: none; color: var(--color-danger, #ff3b30); cursor: pointer; font-size: 1.2em; }
    
    .step-motors { 
        display: grid; 
        grid-template-columns: repeat(4, 1fr); 
        gap: 12px; 
    }
    @media (max-width: 768px) {
        .step-motors { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 480px) {
        .step-motors { grid-template-columns: 1fr; }
    }
    .motor-conf { 
        background: rgba(255,255,255,0.02); 
        border-radius: 6px; 
        padding: 10px; 
        transition: 0.2s; 
        border: 1px solid rgba(255,255,255,0.08); 
    }
    .motor-conf.active { border-color: var(--color-primary, #00f3ff); background: rgba(0,243,255,0.1); }
    .motor-label { 
        font-weight: 600; 
        display: flex; 
        justify-content: space-between; 
        align-items: center; 
        font-size: 0.95em; 
        margin-bottom: 10px; 
        color: var(--color-text, #fff);
    }
    .motor-label input[type="checkbox"] {
        width: 16px;
        height: 16px;
        cursor: pointer;
    }
    .motor-params { 
        display: grid; 
        grid-template-columns: 1fr 1fr; 
        gap: 8px; 
    }
    .motor-params input { 
        width: 100%; 
        font-size: 0.85em; 
        padding: 6px 8px; 
        background: rgba(0,0,0,0.3);
        border: 1px solid rgba(255,255,255,0.15);
        color: inherit;
        border-radius: 4px;
        text-align: center;
    }
    .motor-params input:focus {
        border-color: var(--color-primary, #00f3ff);
        outline: none;
    }
    
    /* Toggle Switch */
    .toggle-switch { position: relative; width: 40px; height: 20px; display: inline-block; }
    .toggle-switch input { opacity: 0; width: 0; height: 0; }
    .toggle-switch label { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #555; transition: .4s; border-radius: 20px; }
    .toggle-switch label:before { position: absolute; content: ""; height: 16px; width: 16px; left: 2px; bottom: 2px; background-color: white; transition: .4s; border-radius: 50%; }
    .toggle-switch input:checked + label { background-color: var(--color-primary, #00f3ff); }
    .toggle-switch input:checked + label:before { transform: translateX(20px); }
</style>
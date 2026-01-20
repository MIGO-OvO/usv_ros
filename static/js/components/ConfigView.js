
import { ref, reactive, watch, toRaw } from '/static/js/lib/vue.esm-browser.prod.js';

export default {
    props: ['config', 'isDirty', 'presets'],
    emits: ['save', 'reset', 'load-preset', 'save-preset', 'delete-preset'],
    template: `
    <div class="config-container">
        <!-- Toolbar -->
        <div class="glass-panel config-toolbar">
            <div class="glass-body" style="display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; gap:12px; align-items:center;">
                    <button class="btn btn-primary" @click="save">保存配置</button>
                    <button class="btn btn-danger" @click="$emit('reset')">重置</button>
                    <span v-if="isDirty.value" style="color:var(--color-warning); font-size:0.9em;">⚠️ 未保存</span>
                </div>
                
                <div class="preset-Area" style="display:flex; gap:8px;">
                    <select v-model="selectedPreset" class="input-glass" style="width:150px;">
                        <option value="">选择预设...</option>
                        <option v-for="p in presets" :value="p">{{p}}</option>
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
                <transition-group name="list" tag="div">
                    <div v-for="(step, index) in config.sampling_sequence.steps" 
                         :key="index" 
                         class="step-item glass-panel"
                         draggable="true"
                         @dragstart="dragStart($event, index)"
                         @dragover.prevent
                         @drop="onDrop($event, index)">
                        
                        <div class="step-header">
                            <span class="step-idx">{{ index + 1 }}</span>
                            <input v-model="step.name" class="input-glass step-name">
                            <input type="number" v-model.number="step.interval" class="input-glass step-interval" title="Interval (ms)"> ms
                            <button class="btn-icon" @click="removeStep(index)">✕</button>
                        </div>
                        
                        <div class="step-motors">
                            <div v-for="axis in ['X','Y','Z','A']" :key="axis" class="motor-conf" :class="{active: step[axis]?.enable === 'E'}">
                                <div class="motor-label">
                                    {{ axis }}
                                    <input type="checkbox" 
                                           :checked="step[axis]?.enable === 'E'"
                                           @change="updateMotorEnable(step, axis, $event.target.checked)">
                                </div>
                                <div v-if="step[axis]?.enable === 'E'" class="motor-params">
                                    <input type="number" v-model.number="step[axis].speed" placeholder="Spd" title="Speed">
                                    <input type="number" v-model.number="step[axis].angle" placeholder="Ang" title="Angle">
                                </div>
                            </div>
                        </div>
                    </div>
                </transition-group>
            </div>
        </div>
    </div>

    <style>
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
        gap: 8px; 
    }
    @media (max-width: 600px) {
        .step-motors { grid-template-columns: repeat(2, 1fr); }
    }
    .motor-conf { 
        background: rgba(255,255,255,0.02); 
        border-radius: 4px; 
        padding: 8px; 
        transition: 0.2s; 
        border: 1px solid rgba(255,255,255,0.05); 
    }
    .motor-conf.active { border-color: var(--color-primary, #00f3ff); background: rgba(0,243,255,0.08); }
    .motor-label { font-weight: bold; display: flex; justify-content: space-between; align-items: center; font-size: 0.9em; margin-bottom: 8px; }
    .motor-params { display: flex; flex-direction: column; gap: 4px; }
    .motor-params input { 
        width: 100%; 
        font-size: 0.85em; 
        padding: 4px 8px; 
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        color: inherit;
        border-radius: 3px;
    }
    
    /* Toggle Switch */
    .toggle-switch { position: relative; width: 40px; height: 20px; display: inline-block; }
    .toggle-switch input { opacity: 0; width: 0; height: 0; }
    .toggle-switch label { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #555; transition: .4s; border-radius: 20px; }
    .toggle-switch label:before { position: absolute; content: ""; height: 16px; width: 16px; left: 2px; bottom: 2px; background-color: white; transition: .4s; border-radius: 50%; }
    .toggle-switch input:checked + label { background-color: var(--color-primary, #00f3ff); }
    .toggle-switch input:checked + label:before { transform: translateX(20px); }
    </style>
    `,
    setup(props, { emit }) {
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
        watch(() => props.config, () => {
            props.isDirty.value = true;
        }, { deep: true });

        return {
            selectedPreset,
            addStep,
            removeStep,
            updateMotorEnable,
            dragStart,
            onDrop,
            save,
            loadPreset,
            savePreset,
            deletePreset
        };
    }
}

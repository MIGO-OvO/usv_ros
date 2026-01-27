<template>
    <div class="dashboard-grid">
        <!-- Status & Control Card -->
        <div class="glass-panel control-card">
            <div class="glass-header">
                <div class="glass-title">任务控制</div>
                <div class="status-tag" :class="status.automation ? 'running' : 'idle'">
                    {{ status.automation ? '任务运行中' : '系统待机' }}
                </div>
            </div>
            <div class="glass-body control-buttons" style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                <button class="btn btn-success" @click="$emit('control', 'start')">
                    <svg viewBox="0 0 24 24" fill="currentColor" width="16"><polygon points="5,3 19,12 5,21" /></svg> 启动
                </button>
                <button class="btn btn-danger" @click="$emit('control', 'stop')">
                    <svg viewBox="0 0 24 24" fill="currentColor" width="16"><rect x="6" y="6" width="12" height="12" /></svg> 停止
                </button>
                <button class="btn btn-warning" @click="$emit('control', 'pause')">
                    <svg viewBox="0 0 24 24" fill="currentColor" width="16"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg> 暂停
                </button>
                <button class="btn btn-primary" @click="$emit('control', 'resume')">
                    <svg viewBox="0 0 24 24" fill="currentColor" width="16"><polygon points="5,3 19,12 5,21" /></svg> 恢复
                </button>
            </div>
        </div>

        <!-- Motors Visualizer -->
        <div class="glass-panel motors-card">
            <div class="glass-header">
                <div class="glass-title">转子监控</div>
            </div>
            <div class="glass-body">
                <div id="motors-chart" style="width:100%; height:250px;"></div>
            </div>
        </div>
        
        <!-- Mini Log (Desktop) -->
        <div class="glass-panel log-mini show-desktop">
             <div class="glass-header">
                <div class="glass-title">实时日志</div>
                <button class="btn btn-sm" @click="$emit('clear-log')" style="font-size:0.8em; padding:4px 8px;">Clear</button>
            </div>
            <div class="glass-body log-body">
                <div v-if="logs.length === 0" style="text-align:center; opacity:0.5; padding:20px;">暂无日志</div>
                <div v-for="log in logs.slice(0, 50)" :key="log.id" class="log-line" :class="log.level">
                    <span class="time">[{{ log.time }}]</span> {{ log.msg }}
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { onMounted, watch, ref, onUnmounted } from 'vue';
import * as echarts from 'echarts';

const props = defineProps(['angles', 'status', 'logs']);
const emit = defineEmits(['control', 'clear-log']);

const chartRef = ref(null);
let chart = null;

const initChart = () => {
    const el = document.getElementById('motors-chart');
    if (!el) return;
    chart = echarts.init(el);

    const option = {
        backgroundColor: 'transparent',
        series: ['X', 'Y', 'Z', 'A'].map((name, idx) => ({
            type: 'gauge',
            center: [`${12 + idx * 25}%`, '55%'],
            radius: '70%',
            startAngle: 90,
            endAngle: -270,
            min: 0, max: 360,
            pointer: { show: true, width: 3 },
            progress: { show: false },
            axisLine: { lineStyle: { width: 5, color: [[1, '#ffffff20']] } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            title: { show: true, offsetCenter: [0, '120%'], color: '#fff', fontSize: 14 },
            detail: { show: true, offsetCenter: [0, '0%'], color: '#00f3ff', fontSize: 16, formatter: '{value}°' },
            data: [{ value: 0, name }]
        }))
    };
    chart.setOption(option);
};

const updateChart = () => {
    if (!chart) return;
    const degrees = [props.angles.X, props.angles.Y, props.angles.Z, props.angles.A];
    chart.setOption({
        series: degrees.map((val, idx) => ({
            data: [{ value: Math.round(val), name: ['X', 'Y', 'Z', 'A'][idx] }]
        }))
    });
};

onMounted(() => {
    setTimeout(initChart, 100);
    window.addEventListener('resize', () => chart && chart.resize());
});

watch(() => props.angles, updateChart, { deep: true });
</script>

<style scoped>
    .dashboard-grid {
        display: grid;
        grid-template-columns: 300px 1fr;
        grid-template-rows: auto 1fr;
        gap: var(--spacing-lg);
        height: 100%;
    }
    .control-card { grid-row: 1; }
    .motors-card { grid-column: 2; grid-row: 1 / span 2; }
    .log-mini { grid-column: 1; grid-row: 2; overflow: hidden; display: flex; flex-direction: column; }
    
    .status-tag {
        padding: 4px 12px; border-radius: 4px; font-size: 0.8em; font-weight: bold;
    }
    .status-tag.running { background: rgba(0, 255, 157, 0.2); color: var(--color-success); border: 1px solid var(--color-success); box-shadow: 0 0 10px var(--color-success); animation: pulse 2s infinite; }
    .status-tag.idle { background: rgba(255,255,255,0.1); color: var(--color-text-muted); }
    
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.6; } 100% { opacity: 1; } }

    .log-body { flex: 1; overflow-y: auto; font-family: 'Fira Code', monospace; font-size: 0.85em; }
    .log-line { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
    .log-line.error { color: var(--color-danger); }
    .log-line.warning { color: var(--color-warning); }
    .log-line.success { color: var(--color-success); }
    .log-line .time { color: var(--color-text-muted); margin-right: 8px; }
    
    @media (max-width: 768px) {
        .dashboard-grid { grid-template-columns: 1fr; grid-template-rows: auto auto; }
        .control-card { grid-row: 1; }
        .motors-card { grid-column: 1; grid-row: 2; height: 300px; }
        .log-mini { display: none; }
    }
</style>
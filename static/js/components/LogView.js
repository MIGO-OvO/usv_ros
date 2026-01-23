
export default {
    props: ['logs'],
    emits: ['clear'],
    template: `
    <div class="glass-panel" style="height:100%; display:flex; flex-direction:column;">
        <div class="glass-header">
            <div class="glass-title">系统日志</div>
            <button class="btn btn-sm" @click="$emit('clear')">清空</button>
        </div>
        <div class="glass-body terminal-body">
            <div v-for="log in logs" :key="log.id" class="term-line">
                <span class="term-time">{{ log.time }}</span>
                <span class="term-level" :class="log.level">[{{ log.level.toUpperCase() }}]</span>
                <span class="term-msg">{{ log.msg }}</span>
            </div>
        </div>
    </div>
    
    <style>
    .terminal-body {
        flex: 1;
        background: rgba(0,0,0,0.5);
        margin: var(--spacing-md);
        border-radius: var(--radius-sm);
        overflow-y: auto;
        font-family: 'Fira Code', monospace;
        font-size: 0.9em;
        line-height: 1.5;
        color: #ddd;
    }
    .term-line { display: flex; gap: 10px; border-bottom: 1px solid rgba(255,255,255,0.02); }
    .term-time { color: #666; }
    .term-level.info { color: #00f3ff; }
    .term-level.success { color: #00ff9d; }
    .term-level.warning { color: #ffb800; }
    .term-level.error { color: #ff0055; }
    </style>
    `
}

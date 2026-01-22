
import { computed } from '/static/js/lib/vue.esm-browser.prod.js';

export default {
    props: ['status', 'connection'],
    emits: ['stop-mission'],
    template: `
    <header class="app-header glass-header" role="banner">
        <div class="logo-area">
            <svg class="logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:24px;height:24px;color:var(--color-primary)" aria-hidden="true">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
            <h1 style="margin-left:12px; font-weight:700; color:white; font-size:1.2rem;">USV <span style="color:var(--color-primary); font-weight:300;">PRO</span></h1>
        </div>

        <div class="status-bar" style="display:flex; gap:16px; align-items:center;" role="status" aria-label="系统状态">
             <!-- Connection -->
             <div class="status-pill" :class="{ active: connection.socket }" 
                  role="status">
                <span class="dot" aria-hidden="true"></span> 
                <span class="label show-desktop">服务器</span>
             </div>
             
             <!-- Pump -->
             <div class="status-pill" :class="{ active: connection.pump }"
                  role="status">
                <span class="dot" aria-hidden="true"></span> 
                <span class="label show-desktop">泵连接</span>
             </div>

             <!-- Auto -->
             <div class="status-pill" :class="{ active: connection.automation }"
                  role="status">
                <span class="dot" aria-hidden="true"></span> 
                <span class="label show-desktop">自动化</span>
             </div>
        </div>

        <div class="emergency-area">
             <button class="btn btn-danger btn-icon-only" 
                     @click="$emit('stop-mission')" 
                     aria-label="紧急停止所有电机"
                     title="紧急停止">
                <svg viewBox="0 0 24 24" fill="currentColor" style="width:20px;height:20px;" aria-hidden="true">
                    <rect x="6" y="6" width="12" height="12" />
                </svg>
             </button>
        </div>
    </header>
    `,
    styles: `
    .status-pill {
        display: flex; align-items: center; gap: 6px;
        background: rgba(255,255,255,0.05);
        padding: 4px 10px;
        border-radius: 20px;
        border: 1px solid rgba(255,255,255,0.1);
        font-size: 0.8rem;
        color: var(--color-text-muted);
        transition: all 0.3s ease;
    }
    .status-pill.active {
        background: rgba(0, 243, 255, 0.1);
        border-color: var(--color-primary);
        color: var(--color-primary);
        box-shadow: 0 0 10px rgba(0,243,255,0.2);
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #555; transition: background 0.3s; }
    .status-pill.active .dot { background: var(--color-primary); box-shadow: 0 0 5px var(--color-primary); }
    `
};

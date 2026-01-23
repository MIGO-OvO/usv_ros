const defaultTheme = require('tailwindcss/defaultTheme')

export default {
  darkMode: 'class', // 启用手动类名控制
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        // 工程级中西文混合栈
        sans: [
          'Inter', 
          'PingFang SC', 
          'Microsoft YaHei UI', 
          'Microsoft YaHei', 
          'Noto Sans SC', 
          ...defaultTheme.fontFamily.sans
        ],
        // 数据显示专用栈
        mono: [
          'JetBrains Mono', 
          'Roboto Mono', 
          'Consolas', 
          'Courier New', 
          ...defaultTheme.fontFamily.mono
        ],
      },
      colors: {
        gemini: {
          50: '#f0f4ff',
          100: '#e0eaff',
          200: '#c7d2fe', // Light accent
          800: '#1f2937', // Borders
          900: '#13161f', // Cards / Secondary bg (Gemini Surface)
          950: '#0b0f19', // Main bg (Gemini Deep)
        },
        slate: {
          850: '#1f2937', // Keep for backward compatibility
        }
      }
    },
  },
  plugins: [],
}

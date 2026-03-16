/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['Syne', 'sans-serif'],
        body: ['DM Sans', 'sans-serif'],
        mono: ['DM Mono', 'monospace'],
      },
      colors: {
        brand: {
          50:  '#EEF3FF',
          100: '#D9E4FF',
          200: '#BACBFF',
          300: '#8FABFF',
          400: '#6285FF',
          500: '#3D5EFF',
          600: '#1A3CF5',
          700: '#1430DE',
          800: '#1228B3',
          900: '#15278C',
          950: '#0D1857',
        },
        ink: {
          50:  '#F6F7F9',
          100: '#ECEEF2',
          200: '#D4D8E2',
          300: '#AEB6C5',
          400: '#8390A4',
          500: '#62708A',
          600: '#4D5870',
          700: '#3E4759',
          800: '#353D4C',
          900: '#1E2330',
          950: '#12151E',
        },
        success: '#10B981',
        warning: '#F59E0B',
        danger:  '#EF4444',
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-mesh': 'radial-gradient(at 40% 20%, #1A3CF5 0px, transparent 50%), radial-gradient(at 80% 0%, #3D5EFF 0px, transparent 50%), radial-gradient(at 0% 50%, #1228B3 0px, transparent 50%)',
      },
      boxShadow: {
        'card': '0 1px 3px 0 rgba(0,0,0,0.05), 0 1px 2px -1px rgba(0,0,0,0.05)',
        'card-md': '0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.07)',
        'card-lg': '0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -4px rgba(0,0,0,0.08)',
        'glow': '0 0 40px rgba(61,94,255,0.25)',
        'glow-sm': '0 0 20px rgba(61,94,255,0.15)',
      },
      animation: {
        'fade-up': 'fadeUp 0.5s ease forwards',
        'fade-in': 'fadeIn 0.4s ease forwards',
        'slide-in': 'slideIn 0.3s ease forwards',
        'pulse-slow': 'pulse 3s ease-in-out infinite',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideIn: {
          '0%': { opacity: '0', transform: 'translateX(-10px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
      },
    },
  },
  plugins: [],
}

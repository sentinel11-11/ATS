export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg0: '#070d17',
        bg1: '#0a1424',
        p1: 'rgba(16,29,50,0.72)',
        p2: 'rgba(10,20,35,0.88)',
        line: 'rgba(122,163,220,0.16)',
        line2: 'rgba(122,163,220,0.30)',
        acc: '#3d86ff',
        acc2: '#6aa6ff',
        muted: '#9db4d2',
        dim: '#6f87a6',
        ok: '#34d399',
        warn: '#ffc857',
        bad: '#ff6476',
        cy: '#53c8ef',
      },
      fontFamily: {
        sans: ['Segoe UI Variable Display', 'Segoe UI', 'Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

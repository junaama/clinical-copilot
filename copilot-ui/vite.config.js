import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// Dev server runs on :5173 and proxies /api → http://localhost:8000 (the Python
// LangGraph backend). In production the UI is served as static files and points
// at VITE_AGENT_URL.
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/api/, ''),
            },
        },
    },
    build: {
        outDir: 'dist',
        sourcemap: true,
    },
    test: {
        globals: true,
        environment: 'jsdom',
        setupFiles: ['./src/test-setup.ts'],
        coverage: {
            provider: 'v8',
            reporter: ['text', 'html', 'lcov'],
            include: ['src/api/**/*.ts', 'src/components/AgentMsg.tsx'],
            thresholds: {
                lines: 80,
                functions: 80,
                statements: 80,
                branches: 75,
            },
        },
    },
});

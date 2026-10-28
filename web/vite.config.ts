import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// `npm run dev` forwards the API to a local `epics-server` (EPICS_COOKIE_SECURE=0) on port 8000.
export default defineConfig({ plugins: [react()], server: { proxy: { '/api': 'http://127.0.0.1:8000' } } });

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 별도 P3 UI는 API proxy와 개발 포트를 환경변수로 분리해 P2 화면과 DB를 공유하지 않는다.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8082';

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_DEV_PORT || 5174),
    strictPort: true,
    proxy: {
      '/api': apiProxyTarget,
      '/actuator': apiProxyTarget,
    },
  },
});

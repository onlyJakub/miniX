import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

function canonicalDirectories(server) {
  server.middlewares.use((request, response, next) => {
    const url = new URL(request.url, 'http://localhost');
    if (['GET', 'HEAD'].includes(request.method) && ['/feed', '/login', '/register'].includes(url.pathname)) {
      response.writeHead(308, { Location: `${url.pathname}/${url.search}` });
      response.end();
      return;
    }
    next();
  });
}

export default defineConfig({
  appType: 'mpa',
  plugins: [tailwindcss(), {
    name: 'minix-directory-pages',
    configureServer: canonicalDirectories,
    configurePreviewServer: canonicalDirectories,
  }],
  build: {
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./index.html', import.meta.url)),
        feed: fileURLToPath(new URL('./feed/index.html', import.meta.url)),
        login: fileURLToPath(new URL('./login/index.html', import.meta.url)),
        register: fileURLToPath(new URL('./register/index.html', import.meta.url)),
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000' },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
  },
});
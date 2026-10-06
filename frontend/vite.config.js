import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';

// app/ui лежит в git: сервер без npm берёт готовую сборку прямо из коммита.
// Значит правка frontend/src без `npm run build` = рабочий, но УСТАРЕВШИЙ интерфейс
// на проде (на живом сервере так и случилось: подсказка в «Настройках» собиралась
// из старого бандла). Плагин кладёт рядом с ассетами контрольную сумму исходников,
// а тест tests/test_ui_build.py сверяет её — сборка либо актуальна, либо CI красный.
const EXTRA_INPUTS = ['index.html', 'tailwind.config.js', 'postcss.config.js'];

function collectFiles(root) {
  const files = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name.startsWith('.')) continue;
      const abs = path.join(dir, e.name);
      if (e.isDirectory()) walk(abs);
      else files.push(abs);
    }
  };
  for (const rel of EXTRA_INPUTS) {
    const abs = path.join(root, rel);
    if (fs.existsSync(abs)) files.push(abs);
  }
  walk(path.join(root, 'src'));
  return files
    .map((abs) => ({ rel: path.relative(root, abs).split(path.sep).join('/'), abs }))
    .sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
}

export function sourceHash(root) {
  const h = crypto.createHash('sha256');
  for (const { rel, abs } of collectFiles(root)) {
    h.update(Buffer.from(rel + '\0', 'utf8'));
    h.update(fs.readFileSync(abs));
  }
  return h.digest('hex');
}

function buildInfoPlugin() {
  let root = null;
  let outDir = null;
  return {
    name: 'ats-build-info',
    apply: 'build',
    configResolved(cfg) {
      root = cfg.root;
      outDir = path.resolve(cfg.root, cfg.build.outDir);
    },
    closeBundle() {
      if (!root || !outDir) return;
      const info = {
        sourceHash: sourceHash(root),
        builtAt: new Date().toISOString(),
        generator: 'ats/frontend/vite.config.js',
      };
      fs.mkdirSync(outDir, { recursive: true });
      fs.writeFileSync(path.join(outDir, 'build-info.json'), JSON.stringify(info, null, 2) + '\n');
    },
  };
}

export default defineConfig({
  plugins: [react(), buildInfoPlugin()],
  base: './',
  build: {
    outDir: '../app/ui',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});

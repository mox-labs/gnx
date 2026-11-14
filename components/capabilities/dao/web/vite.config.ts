import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

// One classic script with nothing left to load: the element, its styles and its Markdown
// renderer. It is written into the dao skill's assets, which the Python package and the
// plugin both carry; `just dao-book` fails when the committed file is not this build.
export default defineConfig({
	plugins: [svelte({ compilerOptions: { customElement: true, css: 'injected' } })],
	build: {
		target: 'es2022',
		outDir: '../src/dao/assets/skills/dao/assets',
		emptyOutDir: false,
		copyPublicDir: false,
		lib: { entry: 'src/index.ts', formats: ['iife'], name: 'DaoBook', fileName: () => 'dao-book.js' }
	}
});

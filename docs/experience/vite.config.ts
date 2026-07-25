import adapterAuto from '@sveltejs/adapter-auto';
import adapterStatic from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// D22: one tree, two builds. GNX_PUBLIC_BUILD=1 emits the public register as a
// static site for gh-pages (internal routes 404 at load and are skipped by the
// prerenderer); everything else keeps adapter-auto for local/dev.
const PUBLIC_ONLY = process.env.GNX_PUBLIC_BUILD === '1';

/** Base path for the static build, normalised to SvelteKit's `` | `/${string}`. */
function publicBase(): '' | `/${string}` {
	const raw = process.env.BASE_PATH ?? '/gnx';
	if (raw === '' || raw === '/') return '';
	const trimmed = raw.replace(/\/+$/, '');
	return trimmed.startsWith('/') ? (trimmed as `/${string}`) : `/${trimmed}`;
}

export default defineConfig({
	// allow importing the canonical convergence surface (docs/convergence/) from outside the app root
	server: { fs: { allow: ['..'] } },
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			adapter: PUBLIC_ONLY
				? adapterStatic({ strict: false, fallback: undefined })
				: adapterAuto(),
			// gh-pages serves at mox-labs.github.io/gnx unless a custom domain lands.
			// SvelteKit types base as `` | `/${string}` — an env override must be
			// normalised to that shape, not asserted into it.
			...(PUBLIC_ONLY ? { paths: { base: publicBase() } } : {})
		})
	]
});

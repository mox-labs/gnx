import { error } from '@sveltejs/kit';
import type { EntryGenerator, PageServerLoad } from './$types';
import { loadCatalog } from '$lib/server/catalog';

// One page per plugin declared in components/bundles.yaml: what it bundles, and how to install it.
export const entries: EntryGenerator = () =>
	loadCatalog().plugins.map((p) => ({ name: p.name }));

export const load: PageServerLoad = ({ params }) => {
	const { plugins, entries } = loadCatalog();
	const plugin = plugins.find((p) => p.name === params.name);
	if (!plugin) throw error(404, `no plugin: ${params.name}`);
	const bySlug = new Map(entries.map((e) => [e.slug, e]));
	return {
		plugin,
		members: plugin.members.map((m) => ({
			...m,
			entry: m.component ? (bySlug.get(m.component) ?? null) : null
		}))
	};
};

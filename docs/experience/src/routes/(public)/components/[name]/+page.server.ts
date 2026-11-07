import { error } from '@sveltejs/kit';
import type { EntryGenerator, PageServerLoad } from './$types';
import { getOverview, loadCatalog } from '$lib/server/catalog';

// One page per catalog component. The page renders the component's own overview, which lives
// beside its code; a component without one yet shows its catalog summary and its source.
export const entries: EntryGenerator = () =>
	loadCatalog().entries.map((e) => ({ name: e.slug }));

export const load: PageServerLoad = ({ params }) => {
	const { entries, plugins } = loadCatalog();
	const entry = entries.find((e) => e.slug === params.name);
	if (!entry) throw error(404, `no component: ${params.name}`);
	return {
		entry,
		overview: getOverview(entry),
		plugins: plugins.filter((p) => p.members.some((m) => m.component === entry.slug))
	};
};

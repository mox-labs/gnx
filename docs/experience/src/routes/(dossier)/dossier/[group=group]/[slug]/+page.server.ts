import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { getDoc } from '$lib/server/content';
import { threadsFor } from '$lib/server/feedback';
import { groupOfSection } from '$lib/registers';

// ONE route for the private wing's doc families: /dossier/{spec|gep|appendix}/<slug>.
// The group is resolved from the doc's own section against the register config — the
// URL can't lie about the register, and a new family costs a config entry, not a route.
export const load: PageServerLoad = ({ params }) => {
	const doc = getDoc(params.slug);
	if (!doc || doc.register === 'public') throw error(404, `no doc: ${params.slug}`);

	const group = groupOfSection(doc.section);
	if (!group || group.id !== params.group)
		throw error(404, `${params.slug} is not in ${params.group}`);

	return { doc, group: group.id, threads: threadsFor(params.slug) };
};

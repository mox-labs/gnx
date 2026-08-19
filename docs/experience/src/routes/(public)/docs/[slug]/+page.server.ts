import { error, redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { getDoc } from '$lib/server/content';
import { threadsFor } from '$lib/server/feedback';

export const load: PageServerLoad = ({ params }) => {
	const doc = getDoc(params.slug);
	if (!doc) throw error(404, `no doc: ${params.slug}`);
	// /docs/* is the public wing. Internal-register docs live in the dossier's appendix —
	// the URL space enforces the register split.
	if (doc.register !== 'public') throw redirect(307, `/dossier/appendix/${params.slug}`);
	return { doc, threads: threadsFor(params.slug) };
};

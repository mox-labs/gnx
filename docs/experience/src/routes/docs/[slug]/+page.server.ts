import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { getDoc } from '$lib/server/content';
import { threadsFor } from '$lib/server/feedback';

export const load: PageServerLoad = ({ params }) => {
	const doc = getDoc(params.slug);
	if (!doc) throw error(404, `no doc: ${params.slug}`);
	return { doc, threads: threadsFor(params.slug) };
};

import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { findConcept, groupOf, conceptBlocks, feedbackDocFor } from '$lib/server/dossier';
import { threadsFor } from '$lib/server/feedback';

export const load: PageServerLoad = ({ params }) => {
	const concept = findConcept(params.slug);
	if (!concept) throw error(404, `no dossier concept: ${params.slug}`);
	const group = groupOf(concept);
	const doc = feedbackDocFor(params.slug);
	return {
		slug: params.slug,
		concept: {
			id: concept.id,
			term: concept.term,
			layer: concept.layer ?? null,
			status: concept.status
		},
		group: group ? { id: group.id, title: group.title } : null,
		blocks: conceptBlocks(concept),
		threads: threadsFor(doc),
		doc
	};
};

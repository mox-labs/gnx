import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { getReview, reviewDocFor, questionCounts } from '$lib/server/review';
import { threadsFor } from '$lib/server/feedback';

export const load: PageServerLoad = ({ params }) => {
	const review = getReview(params.slug);
	if (!review) throw error(404, `no review: ${params.slug}`);
	const doc = reviewDocFor(params.slug);
	const threads = threadsFor(doc);
	// The margin cards are the register now; the header carries only the question tally.
	const counts = questionCounts(threads);
	return {
		meta: review.meta,
		blocks: review.blocks,
		error: review.error ?? null,
		doc,
		threads,
		counts
	};
};

import type { PageServerLoad } from './$types';
import { listReviews, reviewDocFor, questionCounts } from '$lib/server/review';
import { threadsFor } from '$lib/server/feedback';

export const load: PageServerLoad = () => {
	// question counts (open / answered / spawned) per review, from its feedback ledger
	const reviews = listReviews().map((r) => ({
		...r,
		counts: questionCounts(threadsFor(reviewDocFor(r.slug)))
	}));
	return { reviews };
};

import type { PageServerLoad } from './$types';
import { allThreads } from '#lib/server/feedback.ts';

export const load: PageServerLoad = () => {
	return { threads: allThreads() };
};

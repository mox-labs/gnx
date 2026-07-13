import type { PageServerLoad } from './$types';
import { allThreads } from '$lib/server/feedback';

export const load: PageServerLoad = () => {
	return { threads: allThreads() };
};

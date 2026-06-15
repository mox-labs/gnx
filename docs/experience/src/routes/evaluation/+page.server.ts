import type { PageServerLoad } from './$types';
import { getEvaluation } from '$lib/server/content';

export const load: PageServerLoad = () => {
	return { evaluation: getEvaluation() };
};

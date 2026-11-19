import type { PageServerLoad } from './$types';
import { getEvaluation } from '#lib/server/content.ts';

export const load: PageServerLoad = () => {
	return { evaluation: getEvaluation() };
};

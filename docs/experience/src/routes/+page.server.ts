import type { PageServerLoad } from './$types';
import { getCover } from '$lib/server/content';
import { threadsFor } from '$lib/server/feedback';

export const load: PageServerLoad = () => {
	return { cover: getCover(), threads: threadsFor('cover') };
};

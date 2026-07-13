import type { LayoutServerLoad } from './$types';
import { listDocs } from '$lib/server/content';
import { openCounts } from '$lib/server/feedback';
import { dossierNav } from '$lib/server/dossier';

export const load: LayoutServerLoad = () => {
	return { docs: listDocs(), counts: openCounts(), dossier: dossierNav() };
};

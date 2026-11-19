import type { LayoutServerLoad } from './$types';
import { listDocs } from '#lib/server/content.ts';
import { openCounts } from '#lib/server/feedback.ts';
import { dossierNav } from '#lib/server/dossier.ts';

const PUBLIC_ONLY = process.env.GNX_PUBLIC_BUILD === '1';

export const load: LayoutServerLoad = () => {
	return {
		docs: listDocs(),
		counts: openCounts(),
		// D22: the public static build drops every seam into the internal register —
		// the dossier nav never serializes into public page payloads.
		dossier: PUBLIC_ONLY ? [] : dossierNav(),
		publicOnly: PUBLIC_ONLY
	};
};

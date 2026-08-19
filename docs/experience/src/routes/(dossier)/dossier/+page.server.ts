import type { PageServerLoad } from './$types';
// the canonical convergence surface (docs/convergence/) — the dossier IS this, made annotatable
import { surface, tensions } from '../../../../../convergence/surface';
import { openCounts, threadsFor } from '$lib/server/feedback';
import { conceptSlug, feedbackDocFor } from '$lib/dossier/slug';
import { SUMMIT_DOC, summitBlocks } from '$lib/server/dossier';

export const load: PageServerLoad = ({ url }) => {
	// open-comment count per concept (its feedback ledger) — the live "what's under discussion" signal
	const perDoc = openCounts().perDoc;
	const conceptCounts: Record<string, number> = {};
	for (const c of surface.concepts) {
		const n = perDoc[feedbackDocFor(conceptSlug(c.id))];
		if (n) conceptCounts[c.id] = n;
	}
	return {
		surface,
		tensions,
		region: url.searchParams.get('region'),
		conceptCounts,
		// the atlas's own prose is a reading surface too — same Commentable component as the folios
		summit: { doc: SUMMIT_DOC, blocks: summitBlocks(), threads: threadsFor(SUMMIT_DOC) }
	};
};

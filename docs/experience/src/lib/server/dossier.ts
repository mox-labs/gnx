/** Server side of the dossier folio: resolve a concept from the convergence surface and render it as
 *  annotatable blocks (the same DocBlock pipeline the file-backed /docs use, so highlight → margin
 *  thread → file-backed persistence all work). The surface is the single source of truth; converging
 *  is annotating these blocks. */
import { blocksFromMarkdown, type DocBlock } from './content';
import { openCounts } from './feedback';
import { conceptSlug, feedbackDocFor } from '../dossier/slug';
import { surface, tensions as allTensions } from '../../../../convergence/surface';
import type { Entry, Movement, Tension } from '../../../../convergence/surface';

export { conceptSlug, feedbackDocFor };

/** The atlas's own reading prose — the abstract + the foundational fork — as annotatable blocks, so
 *  the landing is a convergence surface too (same Commentable component as every other reading page). */
export const SUMMIT_DOC = 'dossier-summit';
export function summitBlocks(): DocBlock[] {
	return [
		...blocksFromMarkdown(surface.meta.abstract, 'lede'),
		...blocksFromMarkdown(`**The foundational fork.** ${surface.meta.foundationalFork}`, 'fork')
	];
}

/** The dossier spine for the site nav: each region with its concepts (slug, term, status, open-count). */
export function dossierNav() {
	const perDoc = openCounts().perDoc;
	return surface.groups.map((g) => ({
		id: g.id,
		title: g.title,
		concepts: surface.concepts
			.filter((c) => c.group === g.id)
			.map((c) => {
				const slug = conceptSlug(c.id);
				return { slug, term: c.term, status: c.status, open: perDoc[feedbackDocFor(slug)] ?? 0 };
			})
	}));
}

export function findConcept(slug: string): Entry | undefined {
	return surface.concepts.find((c) => conceptSlug(c.id) === slug);
}

export function groupOf(concept: Entry): Movement | undefined {
	return surface.groups.find((g) => g.id === concept.group);
}

export function tensionsOf(concept: Entry): Tension[] {
	return allTensions.filter((t) => concept.tensions.includes(t.id));
}

/** The concept as a folio: the converged interpretant (lede), the body, the readings, and the
 *  decisions owed — each a content-hash-anchored block you can highlight and comment on. */
export function conceptBlocks(concept: Entry): DocBlock[] {
	const blocks: DocBlock[] = [];

	if (concept.convergedInterpretant) {
		blocks.push(...blocksFromMarkdown(concept.convergedInterpretant, 'lede'));
	}
	if (concept.body) {
		blocks.push(...blocksFromMarkdown(concept.body));
	}
	if (concept.readings.length) {
		blocks.push(...blocksFromMarkdown('## The readings — dynamic interpretants'));
		for (const r of concept.readings) {
			const md = `**${r.vein}** — ${r.reading}${r.provenance?.length ? ` _(${r.provenance.join(' · ')})_` : ''}`;
			blocks.push(...blocksFromMarkdown(md, 'reading'));
		}
	}
	const ts = tensionsOf(concept);
	if (ts.length) {
		blocks.push(...blocksFromMarkdown('## Decisions owed'));
		for (const t of ts) {
			blocks.push(...blocksFromMarkdown(`**${t.question}**`, 'owed'));
			blocks.push(...blocksFromMarkdown(t.options.map((o) => `- ${o}`).join('\n')));
			blocks.push(
				...blocksFromMarkdown(`${t.consequence}${t.blocks ? `\n\n_Blocks: ${t.blocks}_` : ''}`)
			);
		}
	}
	return blocks;
}

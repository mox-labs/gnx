/** Client-safe slug helpers for the dossier. No node deps — importable from both the browser
 *  (building hrefs) and server loaders (resolving a concept, keying its feedback ledger). */

const PREFIX = 'gnx.field.';

/** concept id → URL/feedback slug. `gnx.field.two-layer-grammar` → `two-layer-grammar`. */
export function conceptSlug(id: string): string {
	const base = id.startsWith(PREFIX) ? id.slice(PREFIX.length) : id;
	return base.replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '').toLowerCase();
}

/** the feedback ledger key for a concept — namespaced so dossier threads never collide with /docs. */
export function feedbackDocFor(slug: string): string {
	return `dossier-${slug}`;
}

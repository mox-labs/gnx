// The private wing's doc families — configuration, consumed everywhere:
// the param matcher, the /dossier/[group]/[slug] route, and the nav all read this.
// Adding an internal register = one entry here + a content/<section>/ directory.
// Client-safe: no server imports.

export interface RegisterGroup {
	/** URL segment: /dossier/<id>/<slug> */
	id: string;
	/** nav + breadcrumb label */
	label: string;
	/** which content sections belong to this family */
	sections: readonly string[];
}

export const GROUPS: readonly RegisterGroup[] = [
	// gep left this list 2026-07-06 (D17): proposals are public now, served at /docs/<slug>.
	{ id: 'ecosystem', label: 'Ecosystem', sections: ['ecosystem'] },
	{ id: 'spec', label: 'Specs', sections: ['spec'] },
	{ id: 'appendix', label: 'Appendix', sections: ['design', 'build'] }
] as const;

export const groupOfSection = (section: string): RegisterGroup | undefined =>
	GROUPS.find((g) => g.sections.includes(section));

export const groupById = (id: string): RegisterGroup | undefined =>
	GROUPS.find((g) => g.id === id);

/**
 * gnx dossier — the internal documentation, and the surface we converge on.
 *
 * A notebook: it enables comprehension (the entries, grouped into a reading path), surfaces the open
 * decisions (the `open`/`contested` entries carry their question), and holds the intelligence (the
 * readings + the converged understanding). Commentable; we annotate, discuss, and it updates.
 *
 * Data lives in surface.json (rebuilt by the IA pass). This file owns the types + typed exports.
 */
import data from './surface.json';

export type Status = 'settled' | 'converged' | 'contested' | 'open';
export type Confidence = 'high' | 'medium' | 'low';
export type Layer = 'A-declaration' | 'B-meaning' | 'cross';

/** one reading of an entry — a dynamic interpretant (which source, what it says). */
export interface Reading {
	vein: string;
	reading: string;
	provenance?: string[];
}
/** a decision owed — the open-question state of an unresolved entry. */
export interface Tension {
	id: string;
	question: string;
	options: string[];
	owner: 'principal';
	consequence: string;
	blocks?: string;
}
/** an entry — one page of the notebook. */
export interface Entry {
	id: string;
	term: string;
	group: string;
	layer?: Layer;
	status: Status;
	/** one-line thesis */
	convergedInterpretant: string;
	/** the prose — our current understanding */
	body: string;
	readings: Reading[];
	/** open-question ids (only on unresolved entries) → resolve against `tensions` */
	tensions: string[];
}
/** a movement — a section of the reading path. */
export interface Movement {
	id: string;
	title: string;
	blurb: string;
}
export interface GraphNode {
	id: string;
	kind?: string;
	note?: string;
}
export interface GraphEdge {
	from: string;
	to: string;
	label?: string;
}
export interface GraphProposal {
	id: string;
	name: string;
	status: 'proposed';
	confidence: Confidence;
	nodes: GraphNode[];
	edges: GraphEdge[];
	note?: string;
}
export interface Dossier {
	meta: {
		generatedFrom: string;
		/** the summit abstract — a short orientation, not the full converged core */
		abstract: string;
		foundationalFork: string;
		registers: { public: string; internal: string };
	};
	convergedCore: string;
	groups: Movement[];
	concepts: Entry[];
	graphs: GraphProposal[];
}

export const surface = data as unknown as Dossier;
export const tensions = (data as unknown as { tensions: Tension[] }).tensions;
export default surface;

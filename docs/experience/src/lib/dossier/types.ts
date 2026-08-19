/** View-layer types for the dossier — structurally mirror docs/convergence/surface.ts.
 *  The data flows in as a property (the view doesn't own the data). */

export type InterpretantStatus = 'settled' | 'converged' | 'contested' | 'open';
export type Confidence = 'high' | 'medium' | 'low';
export type Layer = 'A-declaration' | 'B-meaning' | 'cross';

export interface Reading {
	vein: string;
	reading: string;
	provenance?: string[];
}
export interface Tension {
	id: string;
	question: string;
	options: string[];
	owner: 'principal';
	consequence: string;
	blocks?: string;
}
export interface Concept {
	id: string;
	term: string;
	group: string;
	layer?: Layer;
	status: InterpretantStatus;
	convergedInterpretant: string;
	body: string;
	readings: Reading[];
	tensions: string[];
}
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
export interface ConvergenceSurface {
	meta: {
		generatedFrom: string;
		abstract: string;
		foundationalFork: string;
		registers: { public: string; internal: string };
	};
	convergedCore: string;
	groups: Movement[];
	concepts: Concept[];
	graphs: GraphProposal[];
}

/** status → trichrome role. settled = established (neutral); converged = resolved (green);
 *  contested = in tension (blue spark); open = decision owed (red). */
export const STATUS_STYLE: Record<InterpretantStatus, { label: string; color: string; bg: string }> = {
	settled: { label: 'settled', color: 'var(--muted)', bg: 'color-mix(in oklab, var(--muted) 12%, transparent)' },
	converged: { label: 'converged', color: 'var(--ci-green)', bg: 'color-mix(in oklab, var(--ci-green) 14%, transparent)' },
	contested: { label: 'contested', color: 'var(--ci-blue)', bg: 'color-mix(in oklab, var(--ci-blue) 14%, transparent)' },
	open: { label: 'open', color: 'var(--ci-red)', bg: 'color-mix(in oklab, var(--ci-red) 14%, transparent)' }
};

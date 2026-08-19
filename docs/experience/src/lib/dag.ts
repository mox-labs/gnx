// The DAG compiler — pure graph work over declared ports, no execution.
// Shared by the Composer widget (synthetic, editable) and the catalog view
// (real manifests, read-only). One compiler, two projections.

export interface DagNode {
	id: string;
	consumes: string[];
	produces: string[];
}

export interface Compiled {
	/** Kahn batches — everything in one batch has its dependencies satisfied and runs in parallel */
	batches: string[][];
	errors: string[];
	/** type → the node ids that produce it */
	producerOf: Record<string, string[]>;
}

export function compile(nodes: DagNode[]): Compiled {
	const errors: string[] = [];

	// producer index + duplicate-output detection
	const producerOf: Record<string, string[]> = {};
	for (const n of nodes) for (const t of n.produces) (producerOf[t] ??= []).push(n.id);
	for (const [t, ps] of Object.entries(producerOf))
		if (ps.length > 1) errors.push(`duplicate output: "${t}" produced by ${ps.join(', ')}`);

	// edges: each consume must have a producer
	const deps: Record<string, Set<string>> = {};
	for (const n of nodes) {
		deps[n.id] = new Set();
		for (const t of n.consumes) {
			const ps = producerOf[t];
			if (!ps) errors.push(`missing producer: ${n.id} consumes "${t}" — nothing produces it`);
			else for (const p of ps) if (p !== n.id) deps[n.id].add(p);
		}
	}

	// Kahn's algorithm → parallel batches; leftover = cycle
	const batches: string[][] = [];
	const remaining = new Set(nodes.map((n) => n.id));
	const satisfied = new Set<string>();
	while (remaining.size) {
		const ready = [...remaining].filter((id) => [...deps[id]].every((d) => satisfied.has(d)));
		if (ready.length === 0) {
			errors.push(`cycle: ${[...remaining].join(' → ')} cannot be ordered`);
			break;
		}
		batches.push(ready);
		for (const id of ready) {
			remaining.delete(id);
			satisfied.add(id);
		}
	}
	return { batches, errors, producerOf };
}

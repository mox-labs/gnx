import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { parse } from 'yaml';
import type { PageServerLoad } from './$types';

// The catalog map (D24): nodes are the REAL manifests — components/ (minted) and
// scratch/composition-validation/drafts/ (proposed) — parsed at load, topology
// derived here exactly the way check.py derives it. No hand-kept census to drift.

const KINDS = new Set(['Skill', 'Agent', 'Capability', 'Flow']);
const VER = /^v[0-9]+$/;

function isTypeUrl(s: string): boolean {
	if (typeof s !== 'string' || s.includes('/')) return false;
	const segs = s.split('.');
	const vi = segs.findIndex((x) => VER.test(x));
	return vi > 0 && vi === segs.length - 2 && /^[a-z0-9]+(-[a-z0-9]+)*$/.test(segs[segs.length - 1]);
}

export type MapNode = {
	id: string;
	tu: string;
	ns: string;
	kind: string;
	status: 'minted' | 'proposed';
	maturity: string;
	description: string;
	ports: string[];
	tags: string[];
	requires: string[];
	uses: string[];
	members?: string[];
	flags: string[];
	x: number;
	y: number;
};

function collect(): Omit<MapNode, 'x' | 'y'>[] {
	const roots = [
		{ dir: path.resolve(process.cwd(), '..', '..', 'components'), status: 'minted' as const, nested: true },
		{
			dir: path.resolve(process.cwd(), '..', '..', 'scratch', 'composition-validation', 'drafts'),
			status: 'proposed' as const,
			nested: false
		}
	];
	const out: Omit<MapNode, 'x' | 'y'>[] = [];
	for (const r of roots) {
		if (!existsSync(r.dir)) continue;
		const files = r.nested
			? readdirSync(r.dir)
					.filter((d) => statSync(path.join(r.dir, d)).isDirectory())
					.map((d) => path.join(r.dir, d, 'manifest.yaml'))
					.filter(existsSync)
			: readdirSync(r.dir)
					.filter((f) => f.endsWith('.yaml'))
					.map((f) => path.join(r.dir, f));
		for (const f of files) {
			let m: Record<string, unknown>;
			try {
				m = parse(readFileSync(f, 'utf8'));
			} catch {
				continue;
			}
			if (!m || typeof m.type_url !== 'string') continue;
			const tu = m.type_url as string;
			const segs = tu.split('.');
			const provides = ((m.provides as string[]) ?? []).map(String);
			const kind = String(m.kind ?? '?');
			const flags: string[] = [];
			if (!KINDS.has(kind)) flags.push(`kind: ${kind} is off-enum`);
			if (tu.startsWith('slick.')) flags.push('namespace call open (vendor vs adoption)');
			out.push({
				id: segs[segs.length - 1],
				tu,
				ns: segs.slice(0, -1).join('.'),
				kind,
				status: r.status,
				maturity: String(m.maturity ?? '—'),
				description: String(m.description ?? ''),
				ports: provides.filter(isTypeUrl),
				tags: provides.filter((p) => !isTypeUrl(p)),
				requires: ((m.requires as string[]) ?? []).map(String),
				uses: (((m.relations as Record<string, string[]>) ?? {}).uses ?? []).map(String),
				members: Array.isArray(m.members)
					? (m.members as { type_url: string }[]).map((x) => x.type_url)
					: undefined,
				flags
			});
		}
	}
	return out;
}

export const load: PageServerLoad = () => {
	// the editable scene — the excalidraw canvas IS the primary map surface (D24)
	let scene: unknown = null;
	try {
		scene = JSON.parse(
			readFileSync(path.resolve(process.cwd(), '..', 'catalog-map', 'generation-0.excalidraw'), 'utf8')
		);
	} catch {
		scene = null;
	}
	const raw = collect();
	const byTu = new Map(raw.map((n) => [n.tu, n]));

	// ---- layout: flows get columns with topo-ordered member stacks; the rest get zones
	const flows = raw.filter((n) => n.kind === 'Flow');
	const memberOf = new Map<string, string>();
	for (const f of flows) for (const m of f.members ?? []) memberOf.set(m, f.tu);

	const NODE_H = 64, GAP = 44, TOP = 120;
	const placed = new Map<string, { x: number; y: number }>();
	const hulls: { x: number; y: number; w: number; h: number; label: string; band?: boolean }[] = [];

	// left column: slick namespace, then minted non-members, then remaining proposed non-members
	// left column in three labeled bands (tufte #4: containment, not floating captions)
	const bands = [
		{ label: 'slick.dev — proposed envelope', ns: raw.filter((n) => n.ns.startsWith('slick') && n.kind !== 'Flow') },
		{ label: 'minted core', ns: raw.filter((n) => !n.ns.startsWith('slick') && n.status === 'minted' && !memberOf.has(n.tu) && n.kind !== 'Flow') },
		{ label: 'proposed — not yet enveloped', ns: raw.filter((n) => !n.ns.startsWith('slick') && n.status === 'proposed' && !memberOf.has(n.tu) && n.kind !== 'Flow') }
	];
	let cy = TOP;
	for (const b of bands) {
		if (!b.ns.length) continue;
		const y0 = cy;
		b.ns.forEach((n) => {
			placed.set(n.tu, { x: 150, y: cy });
			cy += NODE_H + GAP;
		});
		hulls.push({ x: 20, y: y0 - 46, w: 260, h: cy - y0 - GAP + 78, label: b.label, band: true });
		cy += 56;
	}

	flows.forEach((f, fi) => {
		const x = 470 + fi * 300;
		const members = (f.members ?? []).filter((m) => byTu.has(m));
		// topo order by intra-flow port edges (Kahn; declared order breaks ties)
		const provIdx = new Map<string, string>();
		for (const m of members) for (const p of byTu.get(m)!.ports) provIdx.set(p, m);
		const indeg = new Map(members.map((m) => [m, 0]));
		const adj = new Map<string, string[]>(members.map((m) => [m, []]));
		for (const m of members)
			for (const r of byTu.get(m)!.requires) {
				const p = provIdx.get(r);
				if (p && p !== m) {
					adj.get(p)!.push(m);
					indeg.set(m, (indeg.get(m) ?? 0) + 1);
				}
			}
		const order: string[] = [];
		const q = members.filter((m) => !indeg.get(m));
		while (q.length) {
			const m = q.shift()!;
			order.push(m);
			for (const nx of adj.get(m)!) {
				indeg.set(nx, indeg.get(nx)! - 1);
				if (!indeg.get(nx)) q.push(nx);
			}
		}
		for (const m of members) if (!order.includes(m)) order.push(m);
		order.forEach((m, i) => placed.set(m, { x, y: TOP + 40 + i * (NODE_H + GAP) }));
		hulls.push({
			x: x - 130, y: TOP - 4, w: 260, h: 84 + order.length * (NODE_H + GAP),
			label: `→ ${f.tu}`
		});
		placed.set(f.tu, { x, y: TOP + 40 + order.length * (NODE_H + GAP) - 20 });
	});

	const nodes: MapNode[] = raw
		.filter((n) => n.kind !== 'Flow')
		.map((n) => ({ ...n, ...(placed.get(n.tu) ?? { x: 150, y: 100 }) }));

	// ---- derived edges over the whole catalog (provided -> required, string equality)
	// tufte #3: base layer = compiled Flow topology only; cross-catalog matches are
	// hover-layer (xcat) so the map doesn't flood as the roster grows.
	const providers = new Map<string, string[]>();
	for (const n of nodes) for (const p of n.ports) providers.set(p, [...(providers.get(p) ?? []), n.tu]);
	const edges: { s: string; t: string; port?: string; uses?: boolean; xcat?: boolean }[] = [];
	for (const n of nodes)
		for (const r of n.requires)
			for (const p of providers.get(r) ?? []) {
				const sameFlow = memberOf.has(p) && memberOf.get(p) === memberOf.get(n.tu);
				edges.push({ s: p, t: n.tu, port: r.split('.').pop(), xcat: !sameFlow });
			}
	for (const n of nodes)
		for (const u of n.uses) if (byTu.has(u)) edges.push({ s: n.tu, t: u, uses: true });

	const height = Math.max(
		...nodes.map((n) => n.y + 140),
		...hulls.map((h) => h.y + h.h + 60)
	);
	return { scene, nodes, edges, hulls, height, counts: { minted: nodes.filter((n) => n.status === 'minted').length, proposed: nodes.filter((n) => n.status === 'proposed').length, flows: flows.length } };
};

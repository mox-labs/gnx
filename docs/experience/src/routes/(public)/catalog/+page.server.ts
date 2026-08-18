import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { parse } from 'yaml';
import type { PageServerLoad } from './$types';
import { compile, type Compiled } from '$lib/dag';

// The catalog view reads the real manifests on disk — the same source the embryonic
// CLI's load_components reads. Registry surface: real data, read-only, honest maturity.

const REPO_ROOT = path.resolve(process.cwd(), '..', '..');
const COMPONENTS_DIR = path.join(REPO_ROOT, 'components');

export interface CatalogEntry {
	slug: string;
	type_url: string;
	kind: string;
	provides: string[];
	requires: string[];
	/** The subset of `provides` / `requires` that are ports — see `isPort`. */
	produces: string[];
	consumes: string[];
	relations: Record<string, string[]>;
	description: string;
	namespace: string;
	maturity: 'shipped' | 'designed';
}

function oneLine(s: string): string {
	return s.replace(/\s+/g, ' ').trim();
}

/**
 * GEP-0002's shape rule: an entry that parses under GEP-0001's identity grammar is a PORT
 * (it joins — topology, `requires` matching, capability closure); anything else is a
 * discovery TAG (findability only, nothing ever joins on it).
 *
 * GEP-0001 §1/§4: dotted lowercase segments, a medial `v[0-9]+`, and a kebab-case resource
 * after it. Slash-form is rejected (§5); version-terminal is not adopted (§6).
 *
 * A manifest never carries `produces:` / `consumes:` — GEP-0009 §4 defers those, because a
 * runtime speaks that vocabulary and the composer owns the mapping. Topology is derived
 * here, from the shipped five fields, exactly as a composer would derive it.
 */
function isPort(entry: string): boolean {
	if (entry.includes('/')) return false;
	const segs = entry.split('.');
	const vi = segs.findIndex((s) => /^v[0-9]+$/.test(s));
	if (vi <= 0 || vi === segs.length - 1) return false;
	return (
		segs.slice(0, vi).every((s) => /^[a-z][a-z0-9]*$/.test(s)) &&
		segs.slice(vi + 1).every((s) => /^[a-z0-9]+(-[a-z0-9]+)*$/.test(s))
	);
}

/** First sentence of the SKILL.md frontmatter description, if present. */
function descriptionOf(dir: string): string {
	const skill = path.join(dir, 'SKILL.md');
	if (!existsSync(skill)) return '';
	const src = readFileSync(skill, 'utf-8');
	const fm = src.match(/^---\r?\n([\s\S]*?)\r?\n---/);
	if (!fm) return '';
	const desc = fm[1].match(/^description:\s*\|?\s*\n?([\s\S]*?)(?=\n[A-Za-z_-]+:|$)/m);
	if (!desc) return '';
	const first = oneLine(desc[1]).split(/(?<=\.)\s/)[0] ?? '';
	return first.slice(0, 180);
}

/** Every components/<kind-plural>/<slug>/ that holds a manifest (GEP-0009 §1). */
function componentDirs(root: string): string[] {
	if (!existsSync(root)) return [];
	return readdirSync(root)
		.sort()
		.map((k) => path.join(root, k))
		.filter((k) => statSync(k).isDirectory())
		.flatMap((k) =>
			readdirSync(k)
				.sort()
				.map((s) => path.join(k, s))
				.filter((d) => statSync(d).isDirectory() && existsSync(path.join(d, 'manifest.yaml')))
		);
}

function loadEntries(): CatalogEntry[] {
	const out: CatalogEntry[] = [];
	for (const dir of componentDirs(COMPONENTS_DIR)) {
		const name = path.basename(dir);
		const mf = path.join(dir, 'manifest.yaml');
		const m = parse(readFileSync(mf, 'utf-8')) as Record<string, unknown>;
		const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
		const type_url = String(m.type_url ?? name);
		const provides = list(m.provides);
		const requires = list(m.requires);
		out.push({
			slug: name,
			type_url,
			kind: String(m.kind ?? ''),
			provides,
			requires,
			produces: provides.filter(isPort),
			consumes: requires.filter(isPort),
			relations: Object.fromEntries(
				Object.entries((m.relations as Record<string, unknown>) ?? {}).map(([k, v]) => [k, list(v)])
			),
			description: descriptionOf(dir),
			// the namespace prefix is the portability signal
			namespace: type_url.split('.').slice(0, -1).join('.'),
			maturity: m.maturity === 'shipped' ? 'shipped' : 'designed'
		});
	}
	return out;
}

export const load: PageServerLoad = () => {
	const entries = loadEntries();

	// Compositions: only components with topology ports enter the DAG. Skills carry tags
	// alone — provides-only axioms, ambient, never wired — so they fall out by the shape
	// rule rather than by a kind check. An unmet `consumes` compiles to a "missing producer"
	// error and is shown: an unsatisfiable requirement is a true fact about the catalog.
	const wired = entries.filter((e) => e.produces.length || e.consumes.length);
	let composition: Compiled | null = null;
	if (wired.length) {
		composition = compile(
			wired.map((e) => ({ id: e.type_url, consumes: e.consumes, produces: e.produces }))
		);
	}

	return { entries, composition };
};

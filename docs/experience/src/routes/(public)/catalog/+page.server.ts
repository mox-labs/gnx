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

function loadEntries(): CatalogEntry[] {
	if (!existsSync(COMPONENTS_DIR)) return [];
	const out: CatalogEntry[] = [];
	for (const name of readdirSync(COMPONENTS_DIR).sort()) {
		const dir = path.join(COMPONENTS_DIR, name);
		if (!statSync(dir).isDirectory()) continue;
		const mf = path.join(dir, 'manifest.yaml');
		if (!existsSync(mf)) continue;
		const m = parse(readFileSync(mf, 'utf-8')) as Record<string, unknown>;
		const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
		const type_url = String(m.type_url ?? name);
		out.push({
			slug: name,
			type_url,
			kind: String(m.kind ?? ''),
			provides: list(m.provides),
			requires: list(m.requires),
			produces: list(m.produces),
			consumes: list(m.consumes),
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

	// Compositions: only components with topology ports enter the DAG. Skills are
	// provides-only axioms — ambient, never wired. Today both real components are
	// Skills, so this is empty and the page says so honestly.
	const wired = entries.filter((e) => e.produces.length || e.consumes.length);
	let composition: Compiled | null = null;
	if (wired.length) {
		composition = compile(
			wired.map((e) => ({ id: e.type_url, consumes: e.consumes, produces: e.produces }))
		);
	}

	return { entries, composition };
};

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { parse } from 'yaml';
import type { PageServerLoad } from './$types';
import { compile, type Compiled } from '$lib/dag';

// The catalog reads what is actually on disk. Three sources, all sources of record:
//
//   .claude-plugin/marketplace.json  — what a person can install today (generated from
//                                      components/bundles.yaml by `gnx build`)
//   components/<kind>/<slug>/        — the authored components those plugins are cut from
//   .../manifest.yaml                — the identity grammar, where a component carries one
//
// Manifests are DEFERRED, not missing: the Manifest v1 shape is being settled in slick, and
// minting ~70 of them against a moving spec would mean rewriting ~70. So the inventory is
// read from the component directories and the manifest layer is reported as the thin slice
// it currently is. Showing 3 components because only 3 are manifested would be an honest
// query producing a dishonest picture.

const REPO_ROOT = path.resolve(process.cwd(), '..', '..');
const COMPONENTS_DIR = path.join(REPO_ROOT, 'components');
const MARKETPLACE = path.join(REPO_ROOT, '.claude-plugin', 'marketplace.json');
const BUNDLES = path.join(COMPONENTS_DIR, 'bundles.yaml');

/** A component kind directory → the Kind it holds and the file carrying its prose. */
const KIND_DIRS = [
	{ dir: 'capabilities', kind: 'Capability', body: null },
	{ dir: 'agents', kind: 'Agent', body: 'agent.md' },
	{ dir: 'skills', kind: 'Skill', body: 'SKILL.md' },
	{ dir: 'flows', kind: 'Flow', body: null }
] as const;

export interface CatalogEntry {
	slug: string;
	kind: string;
	description: string;
	/** Present only where the component carries a manifest.yaml. */
	type_url: string | null;
	namespace: string | null;
	provides: string[];
	requires: string[];
	/** The subset of `provides` / `requires` that are ports — see `isPort`. */
	produces: string[];
	consumes: string[];
	relations: Record<string, string[]>;
	maturity: 'shipped' | 'designed';
	/** Which installable plugins ship this component. */
	plugins: string[];
	/**
	 * For a Capability: the plugins that ship its *skill*, if any. A capability's runnable
	 * does not project — it is a Python package reached over a transport — so borrowing its
	 * skill's membership into `plugins` would claim you can install the package. You cannot.
	 */
	surfaceIn: string[];
}

export interface PluginEntry {
	name: string;
	description: string;
	version: string;
	category: string;
	keywords: string[];
	agents: number;
	skills: number;
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

/** First sentence of a component body's frontmatter description. */
function descriptionOf(dir: string, bodyFile: string | null): string {
	if (!bodyFile) return '';
	const body = path.join(dir, bodyFile);
	if (!existsSync(body)) return '';
	const src = readFileSync(body, 'utf-8');
	const fm = src.match(/^---\r?\n([\s\S]*?)\r?\n---/);
	if (!fm) return '';
	const desc = fm[1].match(/^description:\s*[|>]?-?\s*\n?([\s\S]*?)(?=\n[A-Za-z_-]+:|$)/m);
	if (!desc) return '';
	const flat = oneLine(desc[1]);
	// Drop the trigger-list preamble the platform requires but a human reader does not.
	const trimmed = flat.replace(/^This skill should be used when |^Use this (agent|skill) when /i, '');
	return (trimmed.split(/(?<=\.)\s/)[0] ?? '').slice(0, 200);
}

/** A capability's one-line purpose, from its pyproject description. */
function capabilityDescription(dir: string): string {
	const pp = path.join(dir, 'pyproject.toml');
	if (!existsSync(pp)) return '';
	const m = readFileSync(pp, 'utf-8').match(/^description\s*=\s*"([^"]*)"/m);
	return m ? oneLine(m[1]).slice(0, 200) : '';
}

function subdirs(root: string): string[] {
	if (!existsSync(root)) return [];
	return readdirSync(root)
		.sort()
		.filter((n) => !n.startsWith('.'))
		.map((n) => path.join(root, n))
		.filter((d) => statSync(d).isDirectory());
}

/**
 * Plugin metadata + which plugins ship which component.
 *
 * Membership comes from `components/bundles.yaml`, not from the projected filenames.
 * A component's identity is its DIRECTORY (`components/agents/claim-extraction/`), but the
 * projector names the emitted file from the agent's frontmatter `name:` — which for that
 * component is `extract`. Keying on the emitted filename therefore reported a bundled
 * component as unbundled. bundles.yaml is the bundling declaration and is directory-keyed,
 * so it is the honest source for this edge.
 *
 * Plugin version/description still come from the generated marketplace.json — that is what
 * a user actually installs, and `just projection` keeps the two in agreement.
 */
function loadPlugins(): {
	plugins: PluginEntry[];
	ships: Map<string, string[]>;
} {
	const ships = new Map<string, string[]>();
	const plugins: PluginEntry[] = [];
	if (!existsSync(BUNDLES)) return { plugins, ships };

	const bundles = parse(readFileSync(BUNDLES, 'utf-8')) as {
		plugins?: Array<Record<string, unknown>>;
	};

	const installable = new Map<string, Record<string, unknown>>();
	if (existsSync(MARKETPLACE)) {
		const mk = JSON.parse(readFileSync(MARKETPLACE, 'utf-8')) as {
			plugins?: Array<Record<string, unknown>>;
		};
		for (const p of mk.plugins ?? []) installable.set(String(p.name ?? ''), p);
	}

	for (const b of bundles.plugins ?? []) {
		const name = String(b.name ?? '');
		const names = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
		const agents = names(b.agents);
		const skills = names(b.skills);
		// `external_skills` are {name, path} entries for a skill authored inside a
		// capability package rather than under components/skills/.
		const external = Array.isArray(b.external_skills)
			? (b.external_skills as Array<Record<string, unknown>>).map((e) => String(e.name ?? ''))
			: [];

		for (const c of [...agents, ...skills, ...external])
			ships.set(c, [...(ships.get(c) ?? []), name]);

		const mkt = installable.get(name);
		plugins.push({
			name,
			description: oneLine(String(mkt?.description ?? b.description ?? '')),
			version: String(mkt?.version ?? b.version ?? ''),
			category: String(b.category ?? ''),
			keywords: names(b.keywords),
			agents: agents.length,
			skills: skills.length + external.length
		});
	}
	return { plugins, ships };
}

function loadEntries(ships: Map<string, string[]>): CatalogEntry[] {
	const out: CatalogEntry[] = [];

	for (const { dir, kind, body } of KIND_DIRS) {
		for (const compDir of subdirs(path.join(COMPONENTS_DIR, dir))) {
			const slug = path.basename(compDir);
			const mf = path.join(compDir, 'manifest.yaml');
			const m = existsSync(mf)
				? (parse(readFileSync(mf, 'utf-8')) as Record<string, unknown>)
				: null;

			const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
			const provides = list(m?.provides);
			const requires = list(m?.requires);
			const type_url = m?.type_url ? String(m.type_url) : null;

			const description =
				kind === 'Capability' ? capabilityDescription(compDir) : descriptionOf(compDir, body);

			out.push({
				slug,
				kind,
				description,
				type_url,
				namespace: type_url ? type_url.split('.').slice(0, -1).join('.') : null,
				provides,
				requires,
				produces: provides.filter(isPort),
				consumes: requires.filter(isPort),
				relations: Object.fromEntries(
					Object.entries((m?.relations as Record<string, unknown>) ?? {}).map(([k, v]) => [
						k,
						list(v)
					])
				),
				maturity: m?.maturity === 'shipped' ? 'shipped' : 'designed',
				plugins: kind === 'Capability' ? [] : (ships.get(slug) ?? []),
				surfaceIn: kind === 'Capability' ? (ships.get(slug) ?? []) : []
			});
		}
	}
	return out;
}

export const load: PageServerLoad = () => {
	const { plugins, ships } = loadPlugins();
	const entries = loadEntries(ships);

	// Compositions: only components with topology ports enter the DAG. Skills carry tags
	// alone — provides-only axioms, ambient, never wired — so they fall out by the shape
	// rule rather than by a kind check. An unmet `consumes` compiles to a "missing producer"
	// error and is shown: an unsatisfiable requirement is a true fact about the catalog.
	const wired = entries.filter((e) => e.type_url && (e.produces.length || e.consumes.length));
	let composition: Compiled | null = null;
	if (wired.length) {
		composition = compile(
			wired.map((e) => ({ id: e.type_url!, consumes: e.consumes, produces: e.produces }))
		);
	}

	return { entries, plugins, composition, manifested: entries.filter((e) => e.type_url).length };
};

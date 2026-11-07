import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { marked } from 'marked';
import { parse } from 'yaml';
import { base } from '$app/paths';
import { compile } from '$lib/dag';
import { sourceUrl, type Catalog, type CatalogEntry, type PluginEntry, type PluginMember } from '$lib/catalog';

// The catalog reads what is actually on disk. Its sources of record:
//
//   .claude-plugin/marketplace.json  — what a person can install today (generated from
//                                      components/bundles.yaml by `gnx build`)
//   components/bundles.yaml          — which plugin bundles which component
//   components/<kind>/<slug>/        — the authored components those plugins are cut from
//   .../manifest.yaml                — the identity grammar, where a component carries one
//   the component's overview         — its own explanation, owned beside its code (below)
//
// The catalog page, the component and plugin pages, and llms.txt all read through this one
// module, so they cannot disagree about what exists.

const REPO_ROOT = path.resolve(process.cwd(), '..', '..');
const COMPONENTS_DIR = path.join(REPO_ROOT, 'components');
const MARKETPLACE = path.join(REPO_ROOT, '.claude-plugin', 'marketplace.json');
const BUNDLES = path.join(COMPONENTS_DIR, 'bundles.yaml');


/**
 * A component kind directory → the Kind it holds, the file carrying its prose, and where its
 * overview lives. A capability is a package with a docs tree; every other kind is a single
 * directory, so its overview sits beside its body.
 */
const KIND_DIRS = [
	{ dir: 'capabilities', kind: 'Capability', body: null, overview: 'docs/explanation/overview.md' },
	{ dir: 'agents', kind: 'Agent', body: 'agent.md', overview: 'overview.md' },
	{ dir: 'skills', kind: 'Skill', body: 'SKILL.md', overview: 'overview.md' },
	{ dir: 'flows', kind: 'Flow', body: null, overview: 'overview.md' }
] as const;

function oneLine(s: string): string {
	return s.replace(/\s+/g, ' ').trim();
}

const rel = (p: string) => path.relative(REPO_ROOT, p).split(path.sep).join('/');

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

/** Split a markdown file into its YAML front matter and body. Malformed YAML reads as none. */
function splitFrontMatter(src: string): { fm: Record<string, unknown>; body: string } {
	const m = src.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
	if (!m) return { fm: {}, body: src };
	let fm: Record<string, unknown> = {};
	try {
		const parsed = parse(m[1]);
		if (parsed && typeof parsed === 'object') fm = parsed as Record<string, unknown>;
	} catch {
		fm = {};
	}
	return { fm, body: src.slice(m[0].length) };
}

/** First sentence of a component body's frontmatter description. */
function descriptionOf(dir: string, bodyFile: string | null): string {
	if (!bodyFile) return '';
	const body = path.join(dir, bodyFile);
	if (!existsSync(body)) return '';
	const desc = splitFrontMatter(readFileSync(body, 'utf-8')).fm.description;
	if (typeof desc !== 'string') return '';
	// Drop the trigger-list preamble the platform requires but a human reader does not.
	const flat = oneLine(desc).replace(
		/^This skill should be used when |^Use this (agent|skill) when /i,
		''
	);
	return (flat.split(/(?<=\.)\s/)[0] ?? '').slice(0, 200);
}

/** A capability's one-line purpose, from its pyproject description. */
function capabilityDescription(dir: string): string {
	const pp = path.join(dir, 'pyproject.toml');
	if (!existsSync(pp)) return '';
	const m = readFileSync(pp, 'utf-8').match(/^description\s*=\s*"([^"]*)"/m);
	return m ? oneLine(m[1]).slice(0, 200) : '';
}

/** A component directory. `_`-prefixed directories are shared support (test helpers), not components. */
function subdirs(root: string): string[] {
	if (!existsSync(root)) return [];
	return readdirSync(root)
		.sort()
		.filter((n) => !n.startsWith('.') && !n.startsWith('_'))
		.map((n) => path.join(root, n))
		.filter((d) => statSync(d).isDirectory());
}

/** The component an in-package skill path belongs to: components/capabilities/<slug>/… */
function owningComponent(repoPath: string): string | null {
	const m = repoPath.match(/^components\/(?:capabilities|skills|agents|flows)\/([^/]+)/);
	return m ? m[1] : null;
}

/**
 * Plugin metadata + which plugins ship which component.
 *
 * Membership comes from `components/bundles.yaml`, not from the projected filenames.
 * A component's identity is its DIRECTORY, but the projector names the emitted file from
 * the agent's frontmatter `name:`, which can differ. bundles.yaml is the bundling
 * declaration and is directory-keyed, so it is the honest source for this edge.
 *
 * Plugin version/description still come from the generated marketplace.json — that is what
 * a user actually installs, and `just projection` keeps the two in agreement.
 */
function loadPlugins(): { plugins: PluginEntry[]; ships: Map<string, string[]> } {
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

	const names = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
	const ship = (component: string, plugin: string) =>
		ships.set(component, [...(ships.get(component) ?? []), plugin]);

	for (const b of bundles.plugins ?? []) {
		const name = String(b.name ?? '');
		const agents = names(b.agents);
		const skills = names(b.skills);
		// `external_skills` are {name, path} entries for a skill authored inside a
		// capability package rather than under components/skills/.
		const external = Array.isArray(b.external_skills)
			? (b.external_skills as Array<Record<string, unknown>>).map((e) => ({
					name: String(e.name ?? ''),
					component: owningComponent(String(e.path ?? ''))
				}))
			: [];

		const members: PluginMember[] = [
			...agents.map((a) => ({ name: a, kind: 'Agent' as const, component: a })),
			...skills.map((s) => ({ name: s, kind: 'Skill' as const, component: s })),
			...external.map((e) => ({ name: e.name, kind: 'Skill' as const, component: e.component }))
		];
		for (const m of members) if (m.component) ship(m.component, name);

		const mkt = installable.get(name);
		plugins.push({
			name,
			description: oneLine(String(mkt?.description ?? b.description ?? '')),
			version: String(mkt?.version ?? b.version ?? ''),
			category: String(b.category ?? ''),
			keywords: names(b.keywords),
			agents: agents.length,
			skills: skills.length + external.length,
			members
		});
	}
	return { plugins, ships };
}

/** The overview's `summary`, if the overview exists and carries one. */
function overviewSummary(file: string): string {
	const s = splitFrontMatter(readFileSync(file, 'utf-8')).fm.summary;
	return typeof s === 'string' ? oneLine(s) : '';
}

function loadEntries(ships: Map<string, string[]>): CatalogEntry[] {
	const out: CatalogEntry[] = [];
	const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);

	for (const { dir, kind, body, overview } of KIND_DIRS) {
		for (const compDir of subdirs(path.join(COMPONENTS_DIR, dir))) {
			const slug = path.basename(compDir);
			const mf = path.join(compDir, 'manifest.yaml');
			const m = existsSync(mf)
				? (parse(readFileSync(mf, 'utf-8')) as Record<string, unknown>)
				: null;

			const ov = path.join(compDir, overview);
			const hasOverview = existsSync(ov);
			const readme = path.join(compDir, 'README.md');

			const provides = list(m?.provides);
			const requires = list(m?.requires);
			const type_url = m?.type_url ? String(m.type_url) : null;

			const description =
				(hasOverview && overviewSummary(ov)) ||
				(typeof m?.description === 'string' ? oneLine(m.description) : '') ||
				(kind === 'Capability' ? capabilityDescription(compDir) : descriptionOf(compDir, body));

			out.push({
				slug,
				kind,
				description,
				dir: rel(compDir),
				overview: hasOverview ? rel(ov) : null,
				readme: existsSync(readme) ? rel(readme) : null,
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

export function loadCatalog(): Catalog {
	const { plugins, ships } = loadPlugins();
	const entries = loadEntries(ships);

	// Compositions: only components with topology ports enter the DAG. Skills carry tags
	// alone — provides-only axioms, ambient, never wired — so they fall out by the shape
	// rule rather than by a kind check. An unmet `consumes` compiles to a "missing producer"
	// error and is shown: an unsatisfiable requirement is a true fact about the catalog.
	const wired = entries.filter((e) => e.type_url && (e.produces.length || e.consumes.length));
	const composition = wired.length
		? compile(wired.map((e) => ({ id: e.type_url!, consumes: e.consumes, produces: e.produces })))
		: null;

	return { entries, plugins, composition, manifested: entries.filter((e) => e.type_url).length };
}

export interface Overview {
	title: string;
	summary: string;
	html: string;
	/** The markdown body, links resolved to absolute URLs — the `.md` twin. */
	markdown: string;
}

/**
 * Repo path of a component's overview → its page on this site, and whether that page renders
 * the overview (so an anchor into it resolves). Null when the path is not an overview of a
 * component that exists.
 */
function componentPageFor(repoPath: string): { url: string; rendered: boolean } | null {
	const m =
		repoPath.match(/^(components\/capabilities\/([^/]+))\/docs\/explanation\/overview\.md$/) ??
		repoPath.match(/^(components\/(?:skills|agents|flows)\/([^/]+))\/overview\.md$/);
	if (!m || !existsSync(path.join(REPO_ROOT, m[1]))) return null;
	return { url: `${base}/components/${m[2]}`, rendered: existsSync(path.join(REPO_ROOT, repoPath)) };
}

/**
 * Resolve a link written in an overview. The overview is authored beside the code, so its
 * relative links are relative to the file in the repository: those become source links, or
 * a component page when they point at another component's overview. Site-absolute links
 * get the deploy base. Anything with a scheme, and in-page anchors, are left alone.
 */
function resolveLink(href: string, fromRepoPath: string): string {
	if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('#') || href.startsWith('//'))
		return href;
	if (href.startsWith('/')) return `${base}${href}`;
	const [p, hash] = href.split('#');
	const target = path.posix.normalize(path.posix.join(path.posix.dirname(fromRepoPath), p));
	const page = componentPageFor(target);
	// An anchor into an overview that is not written yet would point at nothing: link the page.
	if (page) return hash && page.rendered ? `${page.url}#${hash}` : page.url;
	const url = sourceUrl(target);
	return hash ? `${url}#${hash}` : url;
}

/**
 * GitHub's heading anchors (lowercase, punctuation dropped, spaces to hyphens, repeats
 * numbered), so a link written against the file on GitHub, `overview.md#how-it-works`, lands
 * on the same heading here.
 */
function withHeadingIds(html: string): string {
	const seen = new Map<string, number>();
	return html.replace(/<h([1-6])>([\s\S]*?)<\/h\1>/g, (_all, level: string, inner: string) => {
		const text = inner
			.replace(/<[^>]+>/g, '')
			.replace(/&[a-z]+;|&#\d+;/gi, '')
			.toLowerCase()
			.trim();
		const slug = text.replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-');
		const n = seen.get(slug) ?? 0;
		seen.set(slug, n + 1);
		const id = n ? `${slug}-${n}` : slug;
		return `<h${level} id="${id}">${inner}</h${level}>`;
	});
}

/** A component's overview, rendered; null when it has none. */
export function getOverview(entry: CatalogEntry): Overview | null {
	if (!entry.overview) return null;
	const src = readFileSync(path.join(REPO_ROOT, entry.overview), 'utf-8');
	const { fm, body } = splitFrontMatter(src);
	// The page supplies the h1 from the front matter, so a leading h1 in the body would repeat it.
	const content = body.replace(/^\s*#\s+[^\n]*\n/, '').trim();
	const from = entry.overview;

	// Markdown links and images, inline `[text](href "title")` and reference `[id]: href`, are
	// resolved in the source, so the HTML and the markdown twin carry the same URLs. Code is
	// skipped: `handlers[name](event)` in a fence is Python, not a link.
	const markdown = content
		.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/)
		.map((part, i) =>
			i % 2
				? part
				: part
						.replace(
							/(!?\[[^\]]*\]\()([^)\s]+)((?:\s+"[^"]*")?\))/g,
							(_all, open: string, href: string, close: string) =>
								`${open}${resolveLink(href, from)}${close}`
						)
						.replace(
							/^(\s{0,3}\[[^\]]+\]:\s*)(\S+)/gm,
							(_all, open: string, href: string) => `${open}${resolveLink(href, from)}`
						)
		)
		.join('');

	return {
		title: typeof fm.title === 'string' && fm.title ? fm.title : entry.slug,
		summary: typeof fm.summary === 'string' ? oneLine(fm.summary) : entry.description,
		html: withHeadingIds(marked.parse(markdown, { async: false }) as string),
		markdown
	};
}

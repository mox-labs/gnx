import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { marked, type Token } from 'marked';

// The app runs from docs/experience; content lives one level up.
const DOCS_ROOT = path.resolve(process.cwd(), '..');
const CONTENT_DIR = path.join(DOCS_ROOT, 'content');

// Hard gate for public deploys (gh-pages): GNX_PUBLIC_BUILD=1 removes every
// internal-register doc from the content graph at the source — nav, /docs,
// dossier loaders, llms.txt, /raw all inherit the exclusion. The internal wing
// exists only in local/dev builds. (D17: internal must never ship publicly.)
const PUBLIC_ONLY = process.env.GNX_PUBLIC_BUILD === '1';

// Two registers, cut by need (not audience). Public sections lead; internal trail.
// Order here is the canonical nav + reading order.
export type Section =
	| 'start'
	| 'guides'
	| 'reference'
	| 'explanation'
	| 'gep'
	| 'ecosystem'
	| 'spec'
	| 'design'
	| 'build';
export type Register = 'public' | 'internal';
export type Status = 'shipped' | 'planned' | 'proposed' | 'mixed';
export type Mode = 'tutorial' | 'how-to' | 'reference' | 'explanation';

// gep is PUBLIC (D17): open design in the open — proposals ship with the product docs.
// ecosystem is the internal alignment wing (D17): semantic architecture, vision,
// integrations — gnx-the-product stays standalone; users never need the cosmology.
const SECTIONS: Section[] = [
	'start',
	'guides',
	'reference',
	'explanation',
	'gep',
	'ecosystem',
	'spec',
	'design',
	'build'
];
const REGISTER_OF: Record<Section, Register> = {
	start: 'public',
	guides: 'public',
	reference: 'public',
	explanation: 'public',
	gep: 'public',
	ecosystem: 'internal',
	spec: 'internal',
	design: 'internal',
	build: 'internal'
};
export const SECTION_LABEL: Record<Section, string> = {
	start: 'Start',
	guides: 'Guides',
	reference: 'Reference',
	explanation: 'Explanation',
	gep: 'Proposals',
	ecosystem: 'Ecosystem',
	spec: 'Specs',
	design: 'Design',
	build: 'The Build'
};

export interface DocBlock {
	id: string;
	html: string;
	excerpt: string;
	/** presentation hint, e.g. 'lede' for the cover abstract, 'composer'/'decision' for islands */
	variant?: string;
	/** payload for data-driven islands (e.g. a decision fence's JSON) */
	data?: unknown;
}

export interface DocMeta {
	slug: string;
	order: number;
	title: string;
	section: Section;
	register: Register;
	/** maturity marker — the hard honesty gate, rendered as a badge */
	status?: Status;
	/** Diataxis need-type — one per page */
	mode?: Mode;
	fidelity?: string;
}

interface DocEntry extends DocMeta {
	file: string;
}

/** Minimal frontmatter parser — flat `key: value` lines between `---` fences. No YAML dep. */
function parseFrontmatter(src: string): { fm: Record<string, string>; body: string } {
	const m = src.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
	if (!m) return { fm: {}, body: src };
	const fm: Record<string, string> = {};
	for (const line of m[1].split(/\r?\n/)) {
		const mm = line.match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
		if (mm) fm[mm[1]] = mm[2].trim().replace(/^["']|["']$/g, '').replace(/\s+#.*$/, '');
	}
	return { fm, body: src.slice(m[0].length) };
}

function titleFrom(src: string, fallback: string): string {
	const m = src.match(/^#\s+(.+)$/m);
	return m ? m[1].trim() : fallback;
}

function isSection(s: string | undefined): s is Section {
	return !!s && (SECTIONS as string[]).includes(s);
}
function isStatus(s: string | undefined): s is Status {
	return s === 'shipped' || s === 'planned' || s === 'proposed' || s === 'mixed';
}
function isMode(s: string | undefined): s is Mode {
	return s === 'tutorial' || s === 'how-to' || s === 'reference' || s === 'explanation';
}

function readEntry(file: string, fb: { section: Section; order: number; slug: string }): DocEntry {
	const { fm, body } = parseFrontmatter(readFileSync(file, 'utf-8'));
	const section = isSection(fm.section) ? fm.section : fb.section;
	const register: Register =
		fm.register === 'public' || fm.register === 'internal' ? fm.register : REGISTER_OF[section];
	return {
		slug: fm.slug || fb.slug,
		order: fm.order ? Number(fm.order) : fb.order,
		title: fm.title || titleFrom(body, fb.slug),
		section,
		register,
		status: isStatus(fm.status) ? fm.status : undefined,
		mode: isMode(fm.mode) ? fm.mode : undefined,
		fidelity: fm.fidelity || undefined,
		file
	};
}

function entries(): DocEntry[] {
	const out: DocEntry[] = [];

	// The Build surface: the rubric is reviewable like any doc.
	const rubric = path.join(DOCS_ROOT, 'RUBRIC.md');
	if (existsSync(rubric)) out.push(readEntry(rubric, { section: 'build', order: -2, slug: 'rubric' }));

	if (existsSync(CONTENT_DIR)) {
		for (const name of readdirSync(CONTENT_DIR).sort()) {
			const full = path.join(CONTENT_DIR, name);
			if (statSync(full).isDirectory()) {
				// Sectioned subdir: content/<section>/[NN-]slug.md — the new registers.
				const dirSection: Section = isSection(name) ? name : 'design';
				for (const f of readdirSync(full).sort()) {
					if (!f.endsWith('.md') || f.startsWith('_')) continue;
					const m = f.match(/^(?:(\d+)-)?(.+)\.md$/);
					if (!m) continue;
					out.push(
						readEntry(path.join(full, f), {
							section: dirSection,
							order: m[1] ? Number(m[1]) : 50,
							slug: m[2]
						})
					);
				}
				continue;
			}
			// Flat numbered dossier files → the internal Design register.
			const m = name.match(/^(\d+)-(.+)\.md$/);
			if (!m) continue;
			out.push(readEntry(full, { section: 'design', order: Number(m[1]), slug: m[2] }));
		}
	}

	// Ground truth is the internal factual authority — it anchors the Design register.
	const gt = path.join(DOCS_ROOT, 'ground-truth.md');
	if (existsSync(gt)) out.push(readEntry(gt, { section: 'design', order: 99, slug: 'ground-truth' }));

	// The numbered design docs (01–15) are superseded by the dossier concepts — the single source of
	// truth (docs/convergence/surface.json). They're retired from the live site (nav, pager, /docs,
	// llms.txt) so there's no second, stale copy; ground-truth (order 99) stays as the appendix. The
	// .md files remain on disk.
	return out
		.filter((d) => !(d.section === 'design' && d.order < 90))
		.filter((d) => !PUBLIC_ONLY || d.register === 'public')
		.sort((a, b) => {
			const sa = SECTIONS.indexOf(a.section);
			const sb = SECTIONS.indexOf(b.section);
			return sa !== sb ? sa - sb : a.order - b.order;
		});
}

export function listDocs(): DocMeta[] {
	return entries().map(({ file: _file, ...meta }) => meta);
}

function parseBlocks(src: string, variant?: string): DocBlock[] {
	const tokens = marked.lexer(src);
	const seen = new Map<string, number>();
	const blocks: DocBlock[] = [];
	for (const token of tokens as Token[]) {
		if (token.type === 'space') continue;
		// Fenced islands: ```composer (no payload) and ```decision (JSON payload).
		const lang = (token as { lang?: string }).lang;
		if (token.type === 'code' && lang === 'composer') {
			blocks.push({ id: 'composer', html: '', excerpt: '', variant: 'composer' });
			continue;
		}
		if (token.type === 'code' && lang === 'decision') {
			let data: unknown = null;
			try {
				data = JSON.parse((token as { text?: string }).text ?? '{}');
			} catch {
				data = { question: 'malformed decision JSON', alternatives: [], criteria: [], matrix: {} };
			}
			const q = (data as { question?: string })?.question ?? 'decision';
			blocks.push({
				id: 'decision-' + createHash('sha1').update(q).digest('hex').slice(0, 8),
				html: '',
				excerpt: '',
				variant: 'decision',
				data
			});
			continue;
		}
		const html = (marked.parser([token] as never) as string) ?? '';
		if (!html.trim()) continue;
		const raw = (token as { raw?: string }).raw?.trim() ?? '';
		// Content-hash anchors: stable across reorders, change when the block changes —
		// a comment pointing at a changed block is itself signal in the feedback loop.
		const base = createHash('sha1').update(raw).digest('hex').slice(0, 10);
		const n = (seen.get(base) ?? 0) + 1;
		seen.set(base, n);
		blocks.push({
			id: n === 1 ? base : `${base}-${n}`,
			html,
			excerpt: raw.replace(/\s+/g, ' ').slice(0, 160),
			...(variant ? { variant } : {})
		});
	}
	return blocks;
}

/** Turn an arbitrary markdown string into annotatable blocks (content-hash anchored), reusing the
 *  same pipeline the file-backed docs use. The dossier folio builds a concept's blocks through this. */
export function blocksFromMarkdown(src: string, variant?: string): DocBlock[] {
	return parseBlocks(src, variant);
}

export function getDoc(slug: string): (DocMeta & { blocks: DocBlock[] }) | null {
	const entry = entries().find((d) => d.slug === slug);
	if (!entry) return null;
	const { file: _file, ...meta } = entry;
	const { body } = parseFrontmatter(readFileSync(entry.file, 'utf-8'));
	return { ...meta, blocks: parseBlocks(body) };
}

/** Raw markdown body (frontmatter stripped) — the clean .md twin an agent fetches. */
export function getDocSource(slug: string): string | null {
	const entry = entries().find((d) => d.slug === slug);
	if (!entry) return null;
	return parseFrontmatter(readFileSync(entry.file, 'utf-8')).body.trim();
}

export interface EvalRecord {
	file: string;
	verdict: 'SHIP' | 'TIGHTEN' | 'RETURN';
	scores: Record<string, number>;
	edits: number;
	reeval?: boolean;
}

/** The gemini ship-gate scorecard (docs/evaluation.json), in nav order. Empty if not run. */
export function getEvaluation(): EvalRecord[] {
	const f = path.join(DOCS_ROOT, 'evaluation.json');
	if (!existsSync(f)) return [];
	return JSON.parse(readFileSync(f, 'utf-8')) as EvalRecord[];
}


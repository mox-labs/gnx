/** Server side of the review surface — the inquiry twin of the dossier (D20). A dossier folio
 *  equips a ruling; a review renders a research artifact as annotatable blocks and carries an
 *  open-questions register. Reviews reuse the dossier's Commentable pipeline unchanged; the only
 *  new grammar is the question root-mark and the spawn reply. */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { blocksFromMarkdown, type DocBlock } from './content';
import type { Thread } from './feedback';

// Reviews live beside the content graph, one level up from docs/experience.
const DOCS_ROOT = path.resolve(process.cwd(), '..');
const REGISTRY = path.join(DOCS_ROOT, 'reviews.json');

// Same hard gate as the dossier wing (D17): reviews are the internal inquiry register —
// they never ship in the public build.
const PUBLIC_ONLY = process.env.GNX_PUBLIC_BUILD === '1';

export type ReviewStatus = 'flowing' | 'settled';

export interface ReviewMeta {
	slug: string;
	title: string;
	/** source artifact — absolute, or relative to docs/ */
	path: string;
	status: ReviewStatus;
	opened: string;
}

// A review slug must be a legal feedback-ledger key (feedback.ts SLUG_RE).
const SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

/** feedback ledger key for a review — double-dashed namespace so review threads never collide
 *  with a content slug or a dossier concept (`dossier-<slug>`). */
export function reviewDocFor(slug: string): string {
	return `review--${slug}`;
}

function registry(): ReviewMeta[] {
	if (PUBLIC_ONLY || !existsSync(REGISTRY)) return [];
	const raw = JSON.parse(readFileSync(REGISTRY, 'utf-8')) as ReviewMeta[];
	// A malformed slug can't key a ledger file — drop it rather than throw.
	return raw.filter((r) => r && SLUG_RE.test(r.slug));
}

export function listReviews(): ReviewMeta[] {
	return registry();
}

export function findReview(slug: string): ReviewMeta | undefined {
	return registry().find((r) => r.slug === slug);
}

/** A review resolved to annotatable blocks. `error` is set (and blocks empty) when the source
 *  artifact is missing — the page renders an inline error box, never crashes. */
export function getReview(
	slug: string
): { meta: ReviewMeta; blocks: DocBlock[]; error?: string } | null {
	const meta = findReview(slug);
	if (!meta) return null;
	const abs = path.isAbsolute(meta.path) ? meta.path : path.join(DOCS_ROOT, meta.path);
	if (!existsSync(abs)) return { meta, blocks: [], error: `source artifact not found: ${meta.path}` };
	// Finer anchors on the review surface only: split top-level lists into per-item blocks so a
	// margin card anchors at its own list item, not one giant block. (Changes review block ids —
	// public docs + dossier ids are untouched because only this call opts in.)
	return { meta, blocks: blocksFromMarkdown(readFileSync(abs, 'utf-8'), undefined, { splitListItems: true }) };
}

export type QuestionState = 'open' | 'answered' | 'spawned';
export interface QuestionCounts {
	open: number;
	answered: number;
	spawned: number;
}

/** questions = threads whose ROOT mark is a question (items[0].kind). */
export function questionThreads(threads: Thread[]): Thread[] {
	return threads.filter((t) => t.items[0]?.kind === 'question');
}

/** A question's disposition. spawned (a mission was cut) takes precedence over answered
 *  (the thread was resolved) — a spawn is the meaningful outcome even if later resolved. */
export function questionState(t: Thread): QuestionState {
	if (t.items.some((i) => i.kind === 'spawn')) return 'spawned';
	if (t.status === 'resolved') return 'answered';
	return 'open';
}

export function questionCounts(threads: Thread[]): QuestionCounts {
	const c: QuestionCounts = { open: 0, answered: 0, spawned: 0 };
	for (const t of questionThreads(threads)) c[questionState(t)]++;
	return c;
}

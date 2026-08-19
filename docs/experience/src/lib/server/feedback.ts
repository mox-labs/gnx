import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

// Append-only JSONL event ledger per doc; threads are a derived projection.
const FEEDBACK_DIR = path.resolve(process.cwd(), '..', 'feedback');

// question/spawn are the review surface's additions (D20): a question is a root mark like a
// comment; a spawn is a reply that cuts the question into a separate mission.
export type EventKind = 'comment' | 'reply' | 'resolve' | 'reopen' | 'question' | 'spawn';

export interface FeedbackEvent {
	id: string;
	thread: string;
	doc: string;
	bid: string;
	quote: string;
	/** ~32 chars of context either side of the quote — selection-anchored comments only */
	prefix?: string;
	suffix?: string;
	author: string;
	body: string;
	ts: string;
	kind: EventKind;
}

export interface ThreadItem {
	author: string;
	body: string;
	ts: string;
	kind: EventKind;
}

export interface Thread {
	id: string;
	doc: string;
	bid: string;
	quote: string;
	prefix?: string;
	suffix?: string;
	/** true when the comment was anchored to a text selection rather than a whole block */
	anchored: boolean;
	status: 'open' | 'resolved';
	items: ThreadItem[];
	ts: string;
}

const SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

function fileFor(doc: string): string {
	if (!SLUG_RE.test(doc)) throw new Error(`bad doc slug: ${doc}`);
	return path.join(FEEDBACK_DIR, `${doc}.jsonl`);
}

export function appendEvent(
	input: Omit<FeedbackEvent, 'id' | 'ts' | 'thread'> & { thread?: string }
): FeedbackEvent {
	const id = randomUUID();
	const event: FeedbackEvent = {
		...input,
		id,
		thread: input.thread ?? id,
		ts: new Date().toISOString()
	};
	if (!existsSync(FEEDBACK_DIR)) mkdirSync(FEEDBACK_DIR, { recursive: true });
	appendFileSync(fileFor(event.doc), JSON.stringify(event) + '\n', 'utf-8');
	return event;
}

function readEvents(doc: string): FeedbackEvent[] {
	const file = fileFor(doc);
	if (!existsSync(file)) return [];
	return readFileSync(file, 'utf-8')
		.split('\n')
		.filter(Boolean)
		.map((line: string) => JSON.parse(line) as FeedbackEvent);
}

function reduce(events: FeedbackEvent[]): Thread[] {
	const threads = new Map<string, Thread>();
	for (const e of events) {
		// comment and question both open a thread; the root kind (items[0].kind) distinguishes them.
		if (e.kind === 'comment' || e.kind === 'question') {
			threads.set(e.thread, {
				id: e.thread,
				doc: e.doc,
				bid: e.bid,
				quote: e.quote,
				prefix: e.prefix,
				suffix: e.suffix,
				anchored: Boolean(e.prefix !== undefined || e.suffix !== undefined),
				status: 'open',
				items: [{ author: e.author, body: e.body, ts: e.ts, kind: e.kind }],
				ts: e.ts
			});
			continue;
		}
		const t = threads.get(e.thread);
		if (!t) continue;
		if (e.body) t.items.push({ author: e.author, body: e.body, ts: e.ts, kind: e.kind });
		if (e.kind === 'resolve') t.status = 'resolved';
		if (e.kind === 'reopen') t.status = 'open';
	}
	return [...threads.values()];
}

// The annotation ledger is internal discourse (D22): public static builds ship
// pages, never threads — gated here so every consumer inherits it.
const PUBLIC_ONLY = process.env.GNX_PUBLIC_BUILD === '1';

export function threadsFor(doc: string): Thread[] {
	if (PUBLIC_ONLY) return [];
	return reduce(readEvents(doc));
}

export function allThreads(): Record<string, Thread[]> {
	if (PUBLIC_ONLY || !existsSync(FEEDBACK_DIR)) return {};
	const out: Record<string, Thread[]> = {};
	for (const f of readdirSync(FEEDBACK_DIR).sort()) {
		if (!f.endsWith('.jsonl')) continue;
		const doc = f.slice(0, -'.jsonl'.length);
		const threads = threadsFor(doc);
		if (threads.length) out[doc] = threads;
	}
	return out;
}

export function openCounts(): { perDoc: Record<string, number>; total: number } {
	const perDoc: Record<string, number> = {};
	let total = 0;
	for (const [doc, threads] of Object.entries(allThreads())) {
		const n = threads.filter((t) => t.status === 'open').length;
		if (n > 0) {
			perDoc[doc] = n;
			total += n;
		}
	}
	return { perDoc, total };
}

import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { allThreads, appendEvent, threadsFor, type EventKind } from '$lib/server/feedback';

const KINDS: EventKind[] = ['comment', 'reply', 'resolve', 'reopen', 'question', 'spawn'];
// root marks open a thread (need bid + body); the rest act on an existing thread (need thread).
const ROOT_KINDS: EventKind[] = ['comment', 'question'];

export const GET: RequestHandler = ({ url }) => {
	const doc = url.searchParams.get('doc');
	return json(doc ? threadsFor(doc) : allThreads());
};

export const POST: RequestHandler = async ({ request }) => {
	const b = (await request.json()) as Record<string, string>;
	const kind = b.kind as EventKind;
	if (!KINDS.includes(kind)) throw error(400, `kind must be one of ${KINDS.join(', ')}`);
	if (!b.doc) throw error(400, 'doc required');
	if (ROOT_KINDS.includes(kind) && (!b.bid || !b.body)) throw error(400, `${kind} requires bid + body`);
	if (!ROOT_KINDS.includes(kind) && !b.thread) throw error(400, `${kind} requires thread`);
	if ((kind === 'reply' || kind === 'spawn') && !b.body) throw error(400, `${kind} requires body`);

	const event = appendEvent({
		doc: b.doc,
		bid: b.bid ?? '',
		quote: (b.quote ?? '').slice(0, 500),
		...(b.prefix !== undefined ? { prefix: b.prefix.slice(0, 64) } : {}),
		...(b.suffix !== undefined ? { suffix: b.suffix.slice(0, 64) } : {}),
		author: (b.author ?? 'yash').slice(0, 40),
		body: b.body ?? '',
		kind,
		thread: b.thread
	});
	return json(event, { status: 201 });
};

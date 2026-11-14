// The book as `dao book` writes it, and what a reader asks of it.
//
// The data is somebody else's input: a file in a repository, fetched by a page. `parse` takes
// nothing on trust. It keeps the fields it knows, as the types it expects, and refuses a
// format it was not built for.

export const FORMAT = 1;

export type Status = 'PRESENT' | 'PARTIAL' | 'ABSENT';
export type Kind = 'human' | 'agent' | null;

export interface BookDocument {
	key: string;
	name: string;
	file: string;
	summary: string;
	status: Status;
	gaps: string[];
	markdown: string;
}

export interface BookPosition {
	name: string;
	does: string;
	must_not: string;
	checked_by: string[];
	held_by: string[];
	held_only_by_people: boolean;
	checks: string[];
}

export interface Participant {
	name: string;
	kind: Kind;
	positions: string[];
	defined_in: string;
}

export interface Book {
	format: number;
	title: string;
	directory: string;
	complete: boolean;
	sources: string;
	documents: BookDocument[];
	positions: BookPosition[];
	participants: Participant[];
	chains: string[][];
}

export interface Selection {
	kind: 'position' | 'participant';
	name: string;
}

const text = (v: unknown): string => (typeof v === 'string' ? v : '');
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
// A list of names, each once: a name is a key wherever the page lists names.
const texts = (v: unknown): string[] => [
	...new Set(list(v).filter((x): x is string => typeof x === 'string'))
];
/** The first of each key. The documents may repeat a name; the page shows one of each. */
function unique<T>(items: T[], key: (item: T) => string): T[] {
	const seen = new Set<string>();
	return items.filter((item) => {
		const k = key(item);
		if (!k || seen.has(k)) return false;
		seen.add(k);
		return true;
	});
}
const record = (v: unknown): Record<string, unknown> =>
	v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/** A book from parsed JSON. Throws an Error a reader can act on. */
export function parse(value: unknown): Book {
	const raw = record(value);
	if (raw.format !== FORMAT) {
		throw new Error(
			`This book is format ${JSON.stringify(raw.format ?? null)}; this page reads format ${FORMAT}. ` +
				'Run `dao book` again with the version of dao that made the page.'
		);
	}
	const status = (v: unknown): Status => (v === 'PRESENT' || v === 'PARTIAL' ? v : 'ABSENT');
	const kind = (v: unknown): Kind => (v === 'human' || v === 'agent' ? v : null);
	return {
		format: FORMAT,
		title: text(raw.title),
		directory: text(raw.directory),
		complete: raw.complete === true,
		sources: text(raw.sources),
		documents: unique(
			list(raw.documents).map((d) => {
			const r = record(d);
			return {
				key: text(r.key),
				name: text(r.name),
				file: text(r.file),
				summary: text(r.summary),
				status: status(r.status),
				gaps: texts(r.gaps),
				markdown: text(r.markdown)
			};
			}),
			(d) => d.key
		),
		positions: unique(
			list(raw.positions).map((p) => {
				const r = record(p);
				return {
					name: text(r.name),
					does: text(r.does),
					must_not: text(r.must_not),
					checked_by: texts(r.checked_by),
					held_by: texts(r.held_by),
					held_only_by_people: r.held_only_by_people === true,
					checks: texts(r.checks)
				};
			}),
			(p) => p.name
		),
		participants: unique(
			list(raw.participants).map((p) => {
				const r = record(p);
				return {
					name: text(r.name),
					kind: kind(r.kind),
					positions: texts(r.positions),
					defined_in: text(r.defined_in)
				};
			}),
			(p) => p.name
		),
		chains: list(raw.chains).map((c) => list(c).filter((x): x is string => typeof x === 'string'))
	};
}

/** A book from the text of `book.json`. */
export function read(json: string): Book {
	let value: unknown;
	try {
		value = JSON.parse(json);
	} catch {
		throw new Error('The book is not JSON. Run `dao book` to write it again.');
	}
	return parse(value);
}

export const document = (book: Book, key: string): BookDocument | undefined =>
	book.documents.find((d) => d.key === key);

export const position = (book: Book, name: string): BookPosition | undefined =>
	book.positions.find((p) => p.name === name);

export const participant = (book: Book, name: string): Participant | undefined =>
	book.participants.find((p) => p.name === name);

/** What kind of participant holds a position: people, agents, both, or nobody. */
export function holders(book: Book, p: BookPosition): 'human' | 'agent' | 'mixed' | 'vacant' {
	const kinds = new Set(p.held_by.map((n) => participant(book, n)?.kind ?? null));
	if (p.held_by.length === 0) return 'vacant';
	if (kinds.size === 1 && kinds.has('human')) return 'human';
	if (kinds.size === 1 && kinds.has('agent')) return 'agent';
	return 'mixed';
}

/** One link of the map, between two things a reader can select. */
export interface Edge {
	id: string;
	kind: 'holds' | 'checks';
	// holds: participant → position. checks: a position → the position that reads its work.
	from: string;
	to: string;
}

export function edges(book: Book): Edge[] {
	const known = new Set(book.positions.map((p) => p.name));
	// A name may hold any character, so an id is the three parts as JSON, never joined text.
	const id = (kind: Edge['kind'], from: string, to: string): string => JSON.stringify([kind, from, to]);
	const out: Edge[] = [];
	for (const person of book.participants) {
		for (const name of new Set(person.positions)) {
			if (known.has(name)) {
				out.push({ id: id('holds', person.name, name), kind: 'holds', from: person.name, to: name });
			}
		}
	}
	for (const p of book.positions) {
		for (const checker of new Set(p.checked_by)) {
			if (known.has(checker) && checker !== p.name) {
				out.push({ id: id('checks', p.name, checker), kind: 'checks', from: p.name, to: checker });
			}
		}
	}
	return out;
}

export const nodeId = (s: Selection): string => `${s.kind}:${s.name}`;

/** The nodes and edges that belong to a selection: itself, and everything one link away. */
export function neighbourhood(all: Edge[], s: Selection): { nodes: Set<string>; edges: Set<string> } {
	const nodes = new Set([nodeId(s)]);
	const lit = new Set<string>();
	for (const e of all) {
		const a = e.kind === 'holds' ? `participant:${e.from}` : `position:${e.from}`;
		const b = `position:${e.to}`;
		if (a === nodeId(s) || b === nodeId(s)) {
			lit.add(e.id);
			nodes.add(a);
			nodes.add(b);
		}
	}
	return { nodes, edges: lit };
}

/** One `## ` section of a document. The text before the first is the section with no title. */
export interface Section {
	title: string;
	body: string;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
// Two linear steps. One pattern that also dropped the closing hashes took time quadratic in
// the length of a line.
const H2 = /^##[ \t]+(\S.*)$/;
const heading = (text: string): string => text.trimEnd().replace(/(?:^|[ \t])#+$/, '').trimEnd();

export function sections(markdown: string): Section[] {
	const out: Section[] = [{ title: '', body: '' }];
	let fence = '';
	for (const line of markdown.split('\n')) {
		const f = FENCE.exec(line);
		if (f) {
			if (!fence) fence = f[1][0];
			else if (f[1][0] === fence) fence = '';
		}
		const h = fence ? null : H2.exec(line);
		if (h) out.push({ title: heading(h[1]), body: '' });
		else out[out.length - 1].body += line + '\n';
	}
	return out
		.map((s) => ({ title: s.title, body: s.body.trim() }))
		.filter((s) => s.title || s.body);
}

/** Somewhere a reader's words appear. */
export interface Hit {
	kind: 'position' | 'section';
	// position: its name. section: the document's key.
	target: string;
	// section: which of the document's sections.
	index: number;
	label: string;
	context: string;
	before: string;
	match: string;
	after: string;
}

const AROUND = 60;

function excerpt(haystack: string, at: number, length: number): Pick<Hit, 'before' | 'match' | 'after'> {
	const flat = (s: string): string => s.replace(/\s+/g, ' ');
	const start = Math.max(0, at - AROUND);
	const end = Math.min(haystack.length, at + length + AROUND);
	return {
		before: (start > 0 ? '…' : '') + flat(haystack.slice(start, at)),
		match: flat(haystack.slice(at, at + length)),
		after: flat(haystack.slice(at + length, end)) + (end < haystack.length ? '…' : '')
	};
}

/** Where `query` appears: in what a position does or must not do, and in the rules. */
export function search(book: Book, query: string, limit = 12): Hit[] {
	const q = query.trim().toLowerCase();
	if (q.length < 2) return [];
	const hits: Hit[] = [];
	// The first of `haystacks` that holds the words gives the hit, so one place is found once.
	const look = (
		hit: Pick<Hit, 'kind' | 'target' | 'index' | 'label' | 'context'>,
		...haystacks: string[]
	): void => {
		for (const haystack of haystacks) {
			const at = haystack.toLowerCase().indexOf(q);
			if (at < 0) continue;
			hits.push({ ...hit, ...excerpt(haystack, at, q.length) });
			return;
		}
	};
	for (const p of book.positions) {
		const base = { kind: 'position' as const, target: p.name, index: 0, label: p.name };
		look({ ...base, context: 'Position' }, p.name);
		look({ ...base, context: 'Does' }, p.does);
		look({ ...base, context: 'Must not' }, p.must_not);
	}
	for (const key of ['constitution', 'policies', 'principles']) {
		const doc = document(book, key);
		if (!doc) continue;
		sections(doc.markdown).forEach((s, index) => {
			look(
				{ kind: 'section', target: key, index, label: s.title || doc.name, context: doc.name },
				s.body,
				s.title
			);
		});
	}
	return hits.slice(0, limit);
}
